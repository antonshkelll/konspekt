"""Explicit live smoke test with synthetic data; never prints API credentials."""
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from websockets.sync.client import connect
from konspekt import config, llm


def main():
    cfg = config.load()
    url, key, model, service = llm.connection(cfg)
    if (service != "Ollama" and not key) or not cfg["deepgram_key"]:
        print("NOT READY: configure the selected LLM and Deepgram in Settings first.")
        return 2
    started = time.monotonic()
    first = []
    answer = []
    def delta(text):
        if not first:
            first.append(time.monotonic() - started)
        answer.append(text)
    try:
        messages = llm.build_messages("", "Файл: synthetic-brief.md\nБюджет пилота: 42 000 рублей. Срок: две недели.",
                                      "Назови бюджет пилота и его срок по файлу контекста. Коротко.")
        llm.stream_answer(key, model, messages, delta, lambda: False, url, service)
        text = "".join(answer)
        if "42000" not in text.replace(" ", "").replace("\u00a0", ""):
            raise RuntimeError("Ответ получен, но ожидаемая сумма из контекста не найдена.")
        print(f"{service} PASS; first text {first[0]:.2f}s; total {time.monotonic()-started:.2f}s")
        print(text)
        params = urlencode(dict(model=cfg["stt_model"], language=cfg["language"],
                                encoding="linear16", sample_rate=16000, channels=1))
        with connect("wss://api.deepgram.com/v1/listen?" + params,
                     additional_headers={"Authorization": "Token " + cfg["deepgram_key"]},
                     proxy=True if cfg.get("deepgram_use_proxy") else None, open_timeout=15) as ws:
            ws.send(bytes(16000))  # 0.5 seconds of synthetic silence, no microphone.
            ws.send(json.dumps({"type": "CloseStream"}))
            while True:
                msg = json.loads(ws.recv(timeout=15))
                if msg.get("type") == "Error":
                    raise RuntimeError("Deepgram returned a streaming error.")
                if msg.get("type") == "Metadata":
                    break
        print("Deepgram PASS; streaming connection and synthetic silence accepted. Real speech still requires a conversation test.")
        return 0
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
