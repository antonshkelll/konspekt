"""Independent WAV batches for Groq: two sources, at most 15 requests/minute."""
import io
import queue
import threading
import wave
from array import array

import httpx
import pyaudiowpatch as pyaudio

from .account import explain_http


class GroqSource:
    def __init__(self, p, device, speaker, cfg, on_text, on_status):
        self.p, self.device, self.speaker = p, device, speaker
        self.cfg, self.on_text, self.on_status = cfg, on_text, on_status
        self.rate = int(device["defaultSampleRate"])
        self.channels = max(1, min(int(device["maxInputChannels"]), 2))
        self._stop = threading.Event()
        self._capture_done = threading.Event()
        self._queue = queue.Queue(maxsize=2)
        self._stream = None
        self._threads = []

    def start(self):
        self._stream = self.p.open(format=pyaudio.paInt16, channels=self.channels,
                                  rate=self.rate, input=True,
                                  input_device_index=self.device["index"],
                                  frames_per_buffer=self.rate // 10)
        for target in (self._capture, self._transcribe):
            thread = threading.Thread(target=target, daemon=True)
            self._threads.append(thread)
            thread.start()

    def _enqueue(self, frames):
        try:
            self._queue.put_nowait(b"".join(frames))
        except queue.Full:
            self.on_status(f"{self.speaker}: API не успевает, фрагмент пропущен.")

    def _capture(self):
        frames = []
        try:
            while not self._stop.is_set():
                data = self._stream.read(self.rate // 10, exception_on_overflow=False)
                if self.channels > 1:
                    samples = array("h", data)
                    data = array("h", (sum(samples[i:i+self.channels]) // self.channels
                                       for i in range(0, len(samples), self.channels))).tobytes()
                frames.append(data)
                if len(frames) >= 80:
                    self._enqueue(frames)
                    frames = []
        except Exception:
            if not self._stop.is_set():
                self.on_status(f"{self.speaker}: захват звука прервался. Перезапусти запись.")
        finally:
            self._stop.set()
            if frames:
                self._enqueue(frames)
            self._capture_done.set()

    def _transcribe(self):
        with httpx.Client(timeout=30) as client:
            while True:
                try:
                    pcm = self._queue.get(timeout=0.3)
                except queue.Empty:
                    if self._capture_done.is_set():
                        return
                    continue
                # Silence filtering avoids fabricated Whisper phrases and unnecessary API calls.
                samples = array("h", pcm)
                if not samples or sum(abs(v) for v in samples) / len(samples) < 60:
                    continue
                buf = io.BytesIO()
                with wave.open(buf, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(self.rate)
                    wav.writeframes(pcm)
                try:
                    r = client.post("https://api.groq.com/openai/v1/audio/transcriptions",
                                    headers={"Authorization": f"Bearer {self.cfg['groq_key']}"},
                                    data={"model": "whisper-large-v3-turbo", "language": self.cfg["language"]},
                                    files={"file": ("audio.wav", buf.getvalue(), "audio/wav")})
                    if r.status_code != 200:
                        self.on_status(explain_http("Groq STT", r.status_code))
                        if r.status_code in (401, 402, 403, 429):
                            self._stop.set()
                            return
                        continue
                    text = r.json().get("text", "").strip()
                    if text:
                        self.on_text(self.speaker, text, True)
                except Exception:
                    self.on_status(f"{self.speaker}: ошибка Groq STT; фрагмент не распознан.")

    def stop(self):
        self._stop.set()
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except OSError:
                pass
        for thread in self._threads:
            thread.join(timeout=35)
