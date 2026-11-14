"""Проверка ключей и остатка на балансе Deepgram и OpenRouter."""
import re

import httpx

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def explain_http(service: str, code: int) -> str:
    if code == 401 or code == 403:
        hint = (" Вставь сам секретный ключ (у Deepgram — 40 символов, показывается один раз "
                "при создании), а не его ID.") if service == "Deepgram" else ""
        return f"{service} не принял ключ.{hint}"
    if code == 402:
        return f"{service}: недостаточно средств на API-балансе (HTTP 402). Проверь баланс в кабинете провайдера."
    if code == 429:
        return f"{service}: слишком много запросов, подожди немного."
    return f"{service} ответил ошибкой {code}."


def check_deepseek(key: str) -> str:
    if not key:
        return "DeepSeek: ключ не указан."
    r = httpx.get("https://api.deepseek.com/user/balance",
                  headers={"Authorization": f"Bearer {key}"}, timeout=10)
    if r.status_code != 200:
        return explain_http("DeepSeek", r.status_code)
    if not r.json().get("is_available", False):
        return "DeepSeek: ключ работает, но средств для API-запросов недостаточно. Проверь баланс."
    return "DeepSeek: ключ работает, баланс доступен для API-запросов."


def check_deepgram(key: str) -> str:
    if not key:
        return "Deepgram: ключ не указан."
    if UUID.fullmatch(key):
        return "Deepgram: похоже, это ID ключа, а не сам ключ. Создай новый ключ и скопируй секрет."
    headers = {"Authorization": f"Token {key}"}
    r = httpx.get("https://api.deepgram.com/v1/projects", headers=headers, timeout=10)
    if r.status_code != 200:
        return explain_http("Deepgram", r.status_code)
    projects = r.json().get("projects", [])
    if not projects:
        return "Deepgram: ключ работает."
    pid = projects[0]["project_id"]
    b = httpx.get(f"https://api.deepgram.com/v1/projects/{pid}/balances", headers=headers, timeout=10)
    if b.status_code != 200:
        return "Deepgram: ключ работает (баланс этим ключом посмотреть нельзя — у ключа роль Member)."
    total = sum(x.get("amount", 0) for x in b.json().get("balances", []))
    return f"Deepgram: ключ работает, на балансе ${total:.2f}."


def check_openrouter(key: str) -> str:
    if not key:
        return "OpenRouter: ключ не указан."
    headers = {"Authorization": f"Bearer {key}"}
    r = httpx.get("https://openrouter.ai/api/v1/key", headers=headers, timeout=10)
    if r.status_code != 200:
        return explain_http("OpenRouter", r.status_code)
    data = r.json().get("data", {})
    msg = f"OpenRouter: ключ работает, потрачено ${data.get('usage', 0):.2f}"
    c = httpx.get("https://openrouter.ai/api/v1/credits", headers=headers, timeout=10)
    if c.status_code == 200:
        d = c.json().get("data", {})
        msg += f", на балансе ${d.get('total_credits', 0) - d.get('total_usage', 0):.2f}"
    if data.get("is_free_tier"):
        msg += ". Баланс не пополнялся — платные модели (Claude) работать не будут, только бесплатные с пометкой :free"
    return msg + "."
