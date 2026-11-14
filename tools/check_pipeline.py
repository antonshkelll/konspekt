"""Synthetic Russian WAV -> live Deepgram -> local Ollama through the app adapter."""
import json
import sys
import time
import wave
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from websockets.sync.client import connect
from konspekt import config, llm

cfg = config.load()
path = Path(__file__).resolve().parent.parent / "output" / "synthetic-russian.wav"
with wave.open(str(path)) as wav:
    assert wav.getsampwidth() == 2
    channels, rate = wav.getnchannels(), wav.getframerate()
    pcm = wav.readframes(wav.getnframes())
params = urlencode(dict(model=cfg["stt_model"], language="ru", encoding="linear16",
                        channels=channels, sample_rate=rate, punctuate="true"))
started = time.monotonic()
parts = []
with connect("wss://api.deepgram.com/v1/listen?" + params,
             additional_headers={"Authorization": "Token " + cfg["deepgram_key"]},
             proxy=None, open_timeout=15) as ws:
    for offset in range(0, len(pcm), 16000):
        ws.send(pcm[offset:offset + 16000])
    ws.send(json.dumps({"type": "CloseStream"}))
    while True:
        event = json.loads(ws.recv(timeout=20))
        if event.get("type") == "Error":
            raise RuntimeError("Deepgram stream failed")
        if event.get("type") == "Results" and event.get("is_final"):
            text = event["channel"]["alternatives"][0].get("transcript", "").strip()
            if text:
                parts.append(text)
        if event.get("type") == "Metadata":
            break
transcript = " ".join(parts)
if not transcript:
    raise RuntimeError("No synthetic speech recognized")
print(f"STT PASS in {time.monotonic()-started:.2f}s: {transcript}", flush=True)
if len(sys.argv) > 1:
    cfg.update(llm_provider="ollama", ollama_model=sys.argv[1])
url, key, model, service = llm.connection(cfg)
started = time.monotonic()
first = []
answer = []
def delta(text):
    if not first:
        first.append(time.monotonic()-started)
        print(f"LLM first text: {first[0]:.2f}s", flush=True)
    answer.append(text)
messages = llm.build_messages(transcript, "Файл: synthetic-brief.md\nБюджет 42000 рублей, срок 2 недели.",
                              "Назови бюджет и срок из разговора. Ответь одним предложением.")
llm.stream_answer(key, model, messages, delta, lambda: False, url, service, max_tokens=100)
print(f"Pipeline PASS; service={service}; model={model}; LLM total={time.monotonic()-started:.2f}s; answer={''.join(answer)}", flush=True)
