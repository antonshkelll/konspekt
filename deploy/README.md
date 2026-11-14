# Своя LLM на сервере

Windows-клиент остаётся на компьютере: там доступны микрофон и звук звонка.
На Linux-сервер выносится Ollama. MobaXterm — клиент SSH, через него можно настроить сервер и туннель.
Этот вариант убирает плату за LLM API, но требует ресурсов сервера. Распознавание пока остаётся в Groq или Deepgram.

1. Установи Docker Engine и Compose по инструкции для своей ОС: https://docs.docker.com/engine/install/
2. Скопируй `compose.yaml` в папку на сервере. В этой папке выполни:

```sh
docker compose up -d
docker compose exec ollama ollama pull qwen3:4b
docker compose exec ollama ollama run qwen3:4b "Ответь коротко по-русски: привет"
```

CPU-вариант работает без GPU; скорость зависит от процессора и памяти. До звонка проверь задержку генерации.
Для GPU настрой NVIDIA Container Toolkit и GPU passthrough по документации Ollama; текущий Compose этого не включает.
Образ использует latest для первого запуска; для воспроизводимого развёртывания зафиксируй проверенный digest.

3. На своём Windows-компьютере открой туннель:

```sh
ssh -N -L 127.0.0.1:11434:127.0.0.1:11434 USER@SERVER
```

В MobaXterm: **Tunneling → New SSH tunnel → Local port forwarding**:
локальный порт 11434, SSH server — твой сервер, remote server 127.0.0.1, remote port 11434.
Если на ПК уже запущена Ollama, используй локальный порт 11435 и тот же порт в настройках приложения.

4. В Konspekt выбери **Ollama**, адрес `http://127.0.0.1:11434/v1`, модель `qwen3:4b`.
Ключ LLM не нужен. Туннель должен оставаться открытым.
Порт сервера привязан к loopback, публичный доступ к API не требуется.

Проверка с ПК при открытом туннеле: `curl http://127.0.0.1:11434/v1/models`.
Остановка: `docker compose down`. Данные модели остаются в named volume.

Документация: https://docs.ollama.com/docker и https://docs.ollama.com/api/openai-compatibility
