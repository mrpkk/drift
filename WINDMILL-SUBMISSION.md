# Подача drift в Windmill Hub

Дата: 13.10.2026. Площадка проверена, отвечает 200.

## Что внутри

| Файл | Что это |
|---|---|
| `/media/Sruti/iamthat/work/drift/windmill_script.py` | скрипт, проверен живьём |

Три функции:

| Функция | Для чего |
|---|---|
| `main(mandate, action, enforce=False)` | одна проверка. `enforce=True` — скрипт сам бросает исключение, и flow в Windmill останавливается сам |
| `check_many(mandate, actions)` | пакет: один мандат против нескольких действий |
| `explain()` | что drift проверяет и чего не проверяет |

Проверено на живом сервисе:

```
в мандате:   allowed=True  rule=-
сверх лимита: allowed=False rule=amount
enforce:     пропустил разрешённое, отклонил запрещённое
пакет из 3:  allowed=False, checked=3, denied=2, правила: amount, currency
```

## Три ошибки, найденные при проверке

Все три сломали бы скрипт **у каждого покупателя**, а не только в нашем
тесте.

**1. Cloudflare 1010.** Без `User-Agent` Cloudflare отвечает 1010 на любое
обращение. Проверка через `curl` этого не показала бы — у curl заголовок
есть. Нашёл потому, что скрипт сам пошёл в сеть из Python.

**2. Формат ответа не тот, что ожидался.** `drift` отдаёт
`verdict: "ALLOW"`, а поля `allowed` в ответе нет. Первый вариант скрипта
искал `allowed` — и защитный код вернул `no_verdict`.

Здесь важно, что защита сработала **правильно**: вместо того чтобы
принять незнакомый ответ за разрешение, скрипт отказал. Отсутствие вердикта
не должно читаться как согласие.

**3. `report.denied` — число, а не список.** Перебор по нему ронял
скрипт на первом же пакетном вызове. Поймано живым пакетным тестом из
трёх действий.

## Текст подачи

```
Title:    Enforce an agent mandate before every action
Script:   windmill_script.py
Category: Utility / Guardrail
```

Описание:

```
A gate for Windmill flows that run AI agents. Stops an action that a
signed mandate does not permit — at the moment the action happens.

Why this exists: protocols like AP2 verify a mandate once, at signing.
The agent then acts on its own, and nothing holds it in bounds. The
authority ends; the permission does not. This script continues the check,
per action.

Checks, in fixed order: amount, payee, audience, currency, window, intent.
The order is deliberate — an exceeded amount is the most likely form of
drift, so it is reported first.

Features:
- `enforce=True` raises on denial, so a flow stops on its own instead of
  relying on the caller to remember to check
- `check_many()` answers one mandate against a batch of actions; the first
  denial usually identifies the whole problem
- Absence of a mandate is a denial, never consent

Runs over HTTP against a live MCP server. Standard library only.

Service: https://drift.rta.lat
Source: https://github.com/mrpkk/drift
100 checks per day free, then 0.005 USDC on Base mainnet.
```

## Почему это стоит публиковать

Windmill — платформа автоматизации с открытым исходным кодом. Скрипты там
находятся **поиском по автоматизации**: человек строит flow с агентом и
ищет, чем ограничить его действия. Это ровно наш случай и ровно тот
запрос, который иначе остаётся без ответа.

Конкурентов с Mandate-гейтом в Windmill Hub нет.

## Куда подавать

Страницы подачи на самой площадке нет. Каналы с <https://hub.windmill.dev>:

- Discord — https://discord.gg/V7PM2YHsPB
- GitHub Discussions — https://github.com/windmill-labs/windmill/discussions

Оба требуют твоего аккаунта. Публиковать от твоего имени я не буду.
