"""Official SIWC runtime over local stdio. No API keys, browser scraping or token export."""
import json
import os
import queue
import subprocess
import sys
import threading
import time
from urllib.request import getproxies
from pathlib import Path


def runtime_dir():
    return Path(sys.executable).parent / "auth-runtime" if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent / "siwc"


def call(request, on_delta=None, cancel=lambda: False, timeout=200):
    root = runtime_dir()
    node = root / "node-bin" / "node.exe"
    if not node.is_file() or not (root / "bridge.mjs").is_file():
        raise RuntimeError("Нет локального модуля входа ChatGPT. Используй полный пакет приложения с папкой auth-runtime.")
    env = dict(os.environ)
    proxy = getproxies().get("https") or getproxies().get("http")
    if proxy:
        env["KONSPEKT_HTTPS_PROXY"] = proxy
    for name in ("OPENAI_API_KEY", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_LOG", "OPENAI_CUSTOM_HEADERS"):
        env.pop(name, None)
    process = subprocess.Popen([str(node), str(root / "bridge.mjs")], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                               encoding="utf-8", env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    events = queue.Queue()
    def read():
        try:
            for line in process.stdout:
                events.put(line)
        finally:
            events.put(None)
    try:
        process.stdin.write(json.dumps(request, ensure_ascii=False))
        process.stdin.close()
        threading.Thread(target=read, daemon=True).start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel():
                return None
            try:
                line = events.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError("Модуль ChatGPT завершился без подтверждения ответа.")
            event = json.loads(line)
            if event.get("type") == "delta" and on_delta:
                on_delta(event["text"])
            elif event.get("type") == "done":
                return event.get("result", {})
            elif event.get("type") == "error":
                code = event.get("code", "unknown")
                raise RuntimeError(f"ChatGPT: {code}. Проверь вход, разрешение использования плана и лимиты в ChatGPT Settings → Usage. Платный API автоматически не используется.")
        raise RuntimeError("Время ожидания ChatGPT истекло. Повтори вход или проверь сеть.")
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)


def stream(model, messages, on_delta, cancel):
    instructions = "\n\n".join(message["content"] for message in messages if message["role"] == "system")
    inputs = [message for message in messages if message["role"] in ("user", "assistant", "developer")]
    return call({"op":"stream", "model":model, "instructions":instructions, "input":inputs}, on_delta, cancel)
