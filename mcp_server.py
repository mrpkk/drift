"""drift — MCP-сервер непрерывного контроля мандата.

Протокол: initialize / tools/list / tools/call. Ничего лишнего — ни SSE, ни
stateful-сессий, ни HTTP-фреймворка. stdlib, ноль зависимостей.

Зачем это нужно
---------------
Агент подписал мандат: кому, сколько, до какого времени. AP2 проверяет мандат
один раз — в момент подписи. Дальше агент действует сам, и между подписью и
последствием ничего не удерживает его в рамках.

drift продолжает проверку. Каждое действие сверяется с полномочием ДО того, как
оно совершится. Отказ — по умолчанию.

Инструменты
----------
  drift_check   — проверить одно действие против мандата
  drift_session  — открыть сессию, отправить пачку действий, получить отчёт
  drift_explain — какие правила вообще действуют и в каком порядке
"""
from __future__ import annotations

import json
from typing import Any

from drift import Action, Drift, Mandate

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "drift", "version": "0.1.0"}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "drift_check",
        "description": (
            "Проверить одно действие агента против подписанного мандата. "
            "Возвращает ALLOW или DENY и имя правила, которое сработало. "
            "Используй ПЕРЕД совершением действия, а не после."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["mandate", "action"],
            "properties": {
                "mandate": {
                    "type": "object",
                    "description": (
                        "Полномочие: payee_id, audience, max_amount_minor, "
                        "currency, valid_from, valid_until, allowed_intents"
                    ),
                },
                "action": {
                    "type": "object",
                    "description": (
                        "Действие: intent, payee_id, audience, amount_minor, "
                        "currency, at (unix seconds)"
                    ),
                },
            },
        },
    },
    {
        "name": "drift_session",
        "description": (
            "Проверить последовательность действий и вернуть отчёт: сколько "
            "разрешено, сколько отклонено, по каким правилам, и отпечаток "
            "цепочки решений как доказательство."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["mandate", "actions"],
            "properties": {
                "mandate": {"type": "object"},
                "actions": {"type": "array", "items": {"type": "object"}},
            },
        },
    },
    {
        "name": "drift_explain",
        "description": (
            "Показать правила проверки в порядке применения. Полезно, чтобы "
            "понять, какое правило сработает первым при нарушении."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _drift_check(args: dict[str, Any]) -> dict[str, Any]:
    d = Drift(Mandate.from_dict(args["mandate"]))
    a = Action.from_dict(args["action"])
    verdict, rule = d.submit(a)
    return {"verdict": verdict, "rule": rule, "report": d.report()}


def _drift_session(args: dict[str, Any]) -> dict[str, Any]:
    d = Drift(Mandate.from_dict(args["mandate"]))
    decisions = []
    for raw in args["actions"]:
        verdict, rule = d.submit(Action.from_dict(raw))
        decisions.append({"verdict": verdict, "rule": rule, "at": raw.get("at")})
    return {"decisions": decisions, "report": d.report()}


def _drift_explain(_: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_matters": True,
        "rules": [
            {"n": 1, "rule": "amount", "why": "превышение лимита — самая частая форма дрейфа"},
            {"n": 2, "rule": "payee", "why": "получатель не тот, что в мандате"},
            {"n": 3, "rule": "audience", "why": "платёж уходит чужому получателю"},
            {"n": 4, "rule": "currency", "why": "валюта подменена"},
            {"n": 5, "rule": "window", "why": "вне срока действия мандата"},
            {"n": 6, "rule": "intent", "why": "намерение не входит в разрешённый список"},
        ],
        "default": "DENY",
        "principle": "правило проверяется ДО действия, а не после",
    }


HANDLERS = {
    "drift_check": _drift_check,
    "drift_session": _drift_session,
    "drift_explain": _drift_explain,
}


def handle_request(req: dict[str, Any]) -> dict[str, Any] | None:
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"unknown tool: {name}"}}
        try:
            out = handler(params.get("arguments") or {})
        except KeyError as e:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": f"missing argument: {e}"}}
        except (ValueError, TypeError) as e:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": str(e)}}
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}]}}

    if method in ("notifications/initialized", "initialized"):
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"unsupported method: {method}"}}


def parse_body(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


def respond(raw: bytes) -> tuple[int, bytes] | None:
    try:
        req = parse_body(raw)
    except (ValueError, UnicodeDecodeError):
        return 400, json.dumps({"jsonrpc": "2.0", "id": None,
                                "error": {"code": -32700, "message": "parse error"}}).encode()
    batch = isinstance(req, list)
    replies = [r for r in (handle_request(x) if isinstance(x, dict) else None
                           for x in (req if batch else [req])) if r is not None]
    if not replies:
        return 202, b""
    payload = replies if batch else replies[0]
    return 200, json.dumps(payload, ensure_ascii=False).encode("utf-8")
