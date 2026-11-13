"""Захват звука (микрофон и системный звук) и потоковая расшифровка через Deepgram.

Каждый источник получает своё соединение с Deepgram, поэтому реплики
сразу подписаны: «Я» (микрофон) и «Собеседники» (звук из колонок).
"""
import json
import threading
import time
from array import array
from urllib.parse import urlencode

import pyaudiowpatch as pyaudio
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from .account import explain_http
from .groq_stt import GroqSource

DEEPGRAM_URL = "wss://api.deepgram.com/v1/listen"


def list_microphones(p: pyaudio.PyAudio) -> list[tuple[int, str]]:
    # WASAPI, а не MME: у MME имена обрезаны до 31 символа и ломается кириллица.
    host = p.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
    mics = []
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["hostApi"] == host and info["maxInputChannels"] > 0 and not info.get("isLoopbackDevice"):
            mics.append((i, info["name"]))
    return mics


def default_microphone(p: pyaudio.PyAudio) -> dict:
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    return p.get_device_info_by_index(wasapi["defaultInputDevice"])


def default_loopback(p: pyaudio.PyAudio) -> dict | None:
    try:
        return p.get_default_wasapi_loopback()
    except (OSError, LookupError, AttributeError):
        pass
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    speakers = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
    for dev in p.get_loopback_device_info_generator():
        if speakers["name"] in dev["name"]:
            return dev
    return None


class Source:
    """Один аудиоисточник -> одно соединение Deepgram."""

    def __init__(self, p, device: dict, speaker: str, cfg: dict, on_text, on_status):
        self.p = p
        self.device = device
        self.speaker = speaker
        self.cfg = cfg
        self.on_text = on_text
        self.on_status = on_status
        self.channels = max(1, min(int(device["maxInputChannels"]), 2))
        self.rate = int(device["defaultSampleRate"])
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._ws = None
        self._stream = None

    def start(self):
        params = {
            "model": self.cfg["stt_model"],
            "language": self.cfg["language"],
            "encoding": "linear16",
            "sample_rate": self.rate,
            "channels": 1,
            "punctuate": "true",
            "smart_format": "true",
            "interim_results": "true",
            "endpointing": 300,
        }
        try:
            self._ws = connect(
                f"{DEEPGRAM_URL}?{urlencode(params)}",
                additional_headers={"Authorization": f"Token {self.cfg['deepgram_key'].strip()}"},
                open_timeout=10,
            )
        except InvalidStatus as e:
            raise RuntimeError(explain_http("Deepgram", e.response.status_code)) from None
        self._stream = self.p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.rate,
            input=True,
            input_device_index=self.device["index"],
            frames_per_buffer=self.rate // 10,
        )
        for target in (self._pump_audio, self._read_results, self._keepalive):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)

    def _pump_audio(self):
        chunk = self.rate // 10
        while not self._stop.is_set():
            try:
                data = self._stream.read(chunk, exception_on_overflow=False)
                if self.channels > 1:
                    # Берём один канал: в звонках речь в обоих одинаковая.
                    data = array("h", data)[0::self.channels].tobytes()
                self._ws.send(data)
            except Exception as e:  # noqa: BLE001
                if not self._stop.is_set():
                    self.on_status(f"{self.speaker}: звук прервался ({e})")
                return

    def _keepalive(self):
        # Системный звук в тишине не отдаёт данных; без KeepAlive Deepgram закроет сокет.
        while not self._stop.wait(5):
            try:
                self._ws.send(json.dumps({"type": "KeepAlive"}))
            except Exception:  # noqa: BLE001
                return

    def _read_results(self):
        try:
            for raw in self._ws:
                msg = json.loads(raw)
                if msg.get("type") != "Results":
                    continue
                alt = msg["channel"]["alternatives"][0]
                text = alt.get("transcript", "").strip()
                if text:
                    self.on_text(self.speaker, text, bool(msg.get("is_final")))
        except Exception as e:  # noqa: BLE001
            if not self._stop.is_set():
                self.on_status(f"{self.speaker}: соединение с Deepgram закрыто ({e})")

    def stop(self):
        self._stop.set()
        try:
            self._ws.send(json.dumps({"type": "CloseStream"}))
            time.sleep(0.3)
            self._ws.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._stream.stop_stream()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass


class Recorder:
    """Запускает и останавливает все источники разом."""

    def __init__(self, cfg: dict, on_text, on_status):
        self.cfg = cfg
        self.on_text = on_text
        self.on_status = on_status
        self.p = pyaudio.PyAudio()
        self.sources: list[Source] = []

    def start(self):
        if self.cfg["mic_device"] is not None:
            mic = self.p.get_device_info_by_index(int(self.cfg["mic_device"]))
        else:
            mic = default_microphone(self.p)
        devices = [(mic, "Я")]
        if self.cfg["capture_system"]:
            loop = default_loopback(self.p)
            if loop:
                devices.append((loop, "Собеседники"))
            else:
                self.on_status("Не нашёл системный звук — пишу только микрофон")
        try:
            for dev, who in devices:
                factory = GroqSource if self.cfg.get("stt_provider") == "groq" else Source
                src = factory(self.p, dev, who, self.cfg, self.on_text, self.on_status)
                self.sources.append(src)
                src.start()
        except Exception:
            self.stop()
            raise

    def stop(self):
        for src in self.sources:
            src.stop()
        self.sources.clear()

    def close(self):
        self.stop()
        self.p.terminate()
