"""Настройки приложения: хранятся в %APPDATA%/Konspekt/config.json."""
import json
import os
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Konspekt"
CONFIG_PATH = APP_DIR / "config.json"
NOTES_DIR = Path.home() / "Documents" / "Konspekt"

DEFAULTS = {
    "overlay_alpha": 25,
    "openai_key": "",
    "openai_model": "gpt-6-luna",
    "deepgram_key": "",
    "openrouter_key": "",
    "groq_key": "",
    "llm_provider": "ollama",
    "stt_provider": "deepgram",
    "deepgram_use_proxy": False,
    "deepseek_key": "",
    "deepseek_model": "deepseek-flash",
    "ollama_url": "http://127.0.0.1:11434/v1",
    "ollama_model": "qwen3.5:4b",
    "groq_model": "openai/gpt-oss-20b",
    "capture_protection": False,
    "skip_taskbar": False,
    "stt_model": "nova-3",
    "language": "ru",
    "llm_model": "anthropic/claude-sonnet-5.5",
    "fast_model": "anthropic/claude-haiku-5.5",
    "mic_device": None,
    "capture_system": True,
    "meeting_context": "",
    "consent_hint": True,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
