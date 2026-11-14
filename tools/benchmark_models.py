"""Explicit small paid OpenAI comparison using only synthetic meeting data."""
import json
import sys
import time
import re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from konspekt import config, llm

cfg = config.load()
assert cfg.get("openai_key"), "Save your OpenAI API key in Settings first"
cases = [
    ("latest_decision", "Я: сначала думали о бюджете 42000 рублей.\nСобеседники: окончательно утверждаем 48000 рублей на 6 участников.",
     "Назови окончательный бюджет и стоимость на одного участника. Коротко."),
    ("unknown_deadline", "Собеседники: бюджет согласовали, но срок пилота сегодня не обсуждали.",
     "Какой точный срок пилота назван в разговоре? Не додумывай.")
]
results = []
for model in ("gpt-6-luna", "gpt-6.1-terra"):
    url, key, _, service = llm.connection(dict(cfg, llm_provider="openai"))
    for name, transcript, question in cases:
        start = time.monotonic()
        first = []
        answer = []
        def delta(text):
            if not first:
                first.append(time.monotonic() - start)
            answer.append(text)
        try:
            llm.stream_answer(key, model, llm.build_messages(transcript, "", question), delta,
                              lambda: False, url, service, max_tokens=140)
            text = "".join(answer)
            normalized = re.sub(r"\s", "", text).lower()
            passed = ("48000" in normalized and "8000" in normalized) if name == "latest_decision" else any(
                part in text.lower() for part in ("не указан", "не назван", "не обсуждал", "нет данных", "не сообщ", "не определ", "не обознач", "не привод", "отсутств"))
            result = dict(model=model, case=name, ok=passed, first_text_s=round(first[0], 3),
                          total_s=round(time.monotonic()-start, 3), answer=text)
        except Exception as exc:
            result = dict(model=model, case=name, ok=False, error=type(exc).__name__)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
out = Path(__file__).resolve().parent.parent / "output" / "openai-benchmark.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
