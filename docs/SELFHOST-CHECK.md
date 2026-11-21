# Self-host: как проверить, что гайд не врёт

Гайд в README говорит «четыре команды». Здесь — что именно проверено и чем.

## Проверка (22.11.2026, Docker 27, python:3.12-slim)

```bash
docker compose build                 # образ собрался
DRIFT_PORT=18095 docker compose up -d  # порт сдвинут: 8095 занят живым сервисом
sleep 12
curl -s http://127.0.0.1:18095/health   # {"status":"ok","service":"drift","version":"0.1.0"}
curl -s -X POST http://127.0.0.1:18095/mcp -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'   # список инструментов
docker compose down
```

Факты из `docker inspect`:
- `user=drift` (uid 10001) — не root;
- `read_only=true` — корневая ФС только на чтение;
- `secopt=[no-new-privileges:true]`;
- статус `Up (healthy)` — healthcheck образа действительно проходит.

## Ошибка, которую поймали

Первый `up` упал с `ModuleNotFoundError: No module named 'subscription'`: в `Dockerfile` не было
`subscription.py` в `COPY`. Образ собирался, контейнер не стартовал. Исправлено; тест
`tests/test_selfhost.py::test_dockerfile_copies_every_local_module` сверяет импорты с COPY,
чтобы это не повторилось.

## Что не проверялось

- сборка на ARM и на другой версии Docker;
- поведение за reverse-proxy с TLS (публикация наружу — решение владельца, по умолчанию порт локальный);
- платный режим x402 (`DRIFT_X402_ENABLED=1`) в контейнере — проверяется отдельно.
