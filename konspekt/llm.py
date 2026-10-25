"""Запросы к моделям через OpenRouter (потоковый ответ)."""
import json

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM = (
    "Ты помощник для рабочих встреч. Тебе дают расшифровку разговора: реплики «Я» — "
    "пользователь, «Собеседники» — остальные участники. Расшифровка может содержать "
    "ошибки распознавания — восстанавливай смысл по контексту. Отвечай по-русски, "
    "коротко и по делу, без воды. Используй списки, если это помогает."
)

PRESETS = {
    "summary": (
        "Сделай итоги встречи: 1) о чём говорили (3–5 пунктов), 2) принятые решения, "
        "3) задачи в формате «кто — что — срок» (если срок не назван, напиши «срок не назван»), "
        "4) открытые вопросы."
    ),
    "catchup": "Я отвлёкся. Перескажи в 3–4 пунктах, что обсуждали за последние несколько минут.",
    "followup": (
        "Напиши короткое follow-up письмо участникам по итогам встречи: благодарность, "
        "решения, задачи со сроками, следующие шаги. Деловой, но живой тон."
    ),
}


def build_messages(transcript: str, context: str, question: str) -> list[dict]:
    parts = []
    if context.strip():
        parts.append(f"Контекст встречи от пользователя:\n{context.strip()}")
    parts.append(f"Расшифровка:\n{transcript.strip() or '(пока пусто)'}")
    parts.append(f"Запрос: {question}")
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def stream_answer(api_key: str, model: str, messages: list[dict], on_delta, cancel) -> None:
    """Стримит ответ; on_delta(str) вызывается на каждый кусок текста."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Title": "Konspekt",
    }
    body = {"model": model, "messages": messages, "stream": True}
    with httpx.stream("POST", OPENROUTER_URL, headers=headers, json=body, timeout=60) as r:
        if r.status_code != 200:
            r.read()
            raise RuntimeError(f"OpenRouter {r.status_code}: {r.text[:300]}")
        for line in r.iter_lines():
            if cancel():
                return
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                return
            try:
                delta = json.loads(data)["choices"][0]["delta"].get("content")
            except (ValueError, KeyError, IndexError):
                continue
            if delta:
                on_delta(delta)
