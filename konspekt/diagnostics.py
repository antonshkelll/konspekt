"""Small synthetic live request: readiness and measured latency, no real meeting data."""
import time
import httpx
from . import llm


def probe(cfg):
    try:
        url, key, model, service = llm.connection(cfg)
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    if service not in ("Ollama", "ChatGPT") and not key.strip():
        return {"ok": False, "message": f"{service}: сначала укажи API-ключ."}
    started = time.monotonic()
    first = []
    answer = []
    def delta(text):
        if not first:
            first.append(time.monotonic() - started)
        answer.append(text)
    try:
        llm.stream_answer(key, model, [
            {"role": "system", "content": "Ответь кратко по-русски, одним предложением."},
            {"role": "user", "content": "Тестовые данные: бюджет 42000 рублей, срок 2 недели. Назови бюджет и срок."}
        ], delta, lambda: False, url, service, max_tokens=120)
        text = "".join(answer)
        if not first or not text.strip():
            raise RuntimeError("Получен пустой ответ.")
        return {"ok": True, "service": service, "model": model,
                "first_text_s": first[0], "total_s": time.monotonic() - started,
                "answer": text, "message": f"{service} / {model}: генерация работает. Первый текст {first[0]:.2f} с, весь ответ {time.monotonic()-started:.2f} с.\n{text}"}
    except Exception as exc:
        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        for name, value in cfg.items():
            if name.endswith("_key") and value:
                detail = detail.replace(value, "[скрыто]")
        return {"ok": False, "message": f"{service}: проверка не пройдена. {detail}"}
