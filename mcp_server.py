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

# Tool descriptions are the product surface for an LLM consumer, so they are English and
# they follow Glama's TDQS dimensions: state the purpose, say when NOT to call, declare
# side effects and idempotency, and give the parameter semantics without external lookup.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "drift_check",
        "annotations": {
            "title": "Check one action against a mandate",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Check ONE proposed action against a signed agent mandate BEFORE it "
            "happens. Returns {\"verdict\": \"ALLOW\"|\"DENY\", \"rule\": <name>} "
            "plus a report with a ledger fingerprint.\n\n"
            "Six rules run in a fixed order, so a refusal names the most important "
            "reason rather than an incidental one: amount, payee, audience, currency, "
            "window, intent. Anything unclassified is DENY.\n\n"
            "Use immediately before any action that spends money, moves a credential "
            "or changes state on someone else's behalf.\n\n"
            "Do NOT use to: verify mandate signatures (AP2 does that, and this server "
            "runs after signature verification), execute the action (it only decides), "
            "or certify that an intent is truthful — \"intent\" arrives from the agent "
            "and is untrusted input. An agent that lies within its limits still leaves "
            "a ledger entry; proving what really happened is attest's job, not this "
            "one's.\n\n"
            "Amounts are integer minor units (cents), never floats. Window bounds are "
            "inclusive. Fully idempotent: the same inputs always give the same verdict "
            "and the mandate is never mutated."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["mandate", "action"],
            "properties": {
                "mandate": {
                    "type": "object",
                    "required": [
                        "payee_id", "audience", "max_amount_minor", "currency",
                        "valid_from", "valid_until", "allowed_intents",
                    ],
                    "properties": {
                        "payee_id": {"type": "string", "description": "Exact payee the mandate names."},
                        "audience": {"type": "string", "description": "Merchant audience URI bound into the mandate."},
                        "max_amount_minor": {"type": "integer", "minimum": 0, "description": "Spending cap in minor units, e.g. 10000 = 100.00 USD. Integers only."},
                        "currency": {"type": "string", "description": "ISO-4217 code, e.g. USD."},
                        "valid_from": {"type": "integer", "description": "Inclusive start, unix seconds."},
                        "valid_until": {"type": "integer", "description": "Inclusive end, unix seconds."},
                        "allowed_intents": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Permitted intents. Empty means any intent is allowed.",
                        },
                    },
                    "description": "The signed authority being enforced. Treat as read-only input.",
                },
                "action": {
                    "type": "object",
                    "required": [
                        "intent", "payee_id", "audience", "amount_minor",
                        "currency", "at",
                    ],
                    "properties": {
                        "intent": {"type": "string", "description": "What the agent says it is doing. Untrusted."},
                        "payee_id": {"type": "string", "description": "Payee the agent is about to use."},
                        "audience": {"type": "string", "description": "Audience the payment would go to."},
                        "amount_minor": {"type": "integer", "minimum": 0, "description": "Amount in minor units, same scale as max_amount_minor."},
                        "currency": {"type": "string", "description": "ISO-4217 code of this payment."},
                        "at": {"type": "integer", "description": "When the action would happen, unix seconds."},
                    },
                    "description": "The action being proposed.",
                },
            },
        },
    },
    {
        "name": "drift_session",
        "annotations": {
            "title": "Check a sequence of actions and fingerprint the ledger",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Check a SEQUENCE of proposed actions against one mandate. Returns "
            "{submitted, allowed, denied, breach_by_rule, fingerprint}.\n\n"
            "Use for a whole plan or batch, not a single action — use drift_check for "
            "that. The fingerprint is the point: rewriting or reordering any earlier "
            "decision changes the hash, so tampering with history becomes visible "
            "instead of silent.\n\n"
            "Do NOT use it to execute the actions, or as evidence of what actually "
            "happened. It reports what was PROPOSED against a mandate you supply.\n\n"
            "Read-only. Idempotent for a fixed action list. Amounts in integer minor "
            "units, as in drift_check."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["mandate", "actions"],
            "properties": {
                "mandate": {"type": "object", "description": "Same shape as in drift_check."},
                "actions": {
                    "type": "array", "items": {"type": "object"},
                    "description": "Proposed actions in order. Order affects the fingerprint.",
                },
            },
        },
    },
    {
        "name": "drift_explain",
        "annotations": {
            "title": "Show the enforcement rules in the order they apply",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Return the six enforcement rules in the exact order they are applied, each "
            "with a plain-language reason it exists. Takes no mandate and no action.\n\n"
            "Use when a refusal did not make sense, or before explaining a denial to "
            "someone whose mandate was rejected. A refusal you cannot explain is one "
            "nobody will trust.\n\n"
            "Do NOT use it to check an action — it performs no check, and cannot predict "
            "which rule will fire, only the order they are tried in.\n\n"
            "Constant output, fully idempotent, opens no files, contacts no host."
        ),
        "inputSchema": {
            "type": "object", "properties": {},
            "description": "No parameters.",
        },
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
            "capabilities": {
                  "tools": {"listChanged": False},
                  "resources": {"listChanged": False},
                  "prompts": {"listChanged": False},
              },
            "serverInfo": SERVER_INFO,
        }}

    if method == "resources/list":
        # Ресурсов у сервера нет. Пустой список — честный ответ: -32601 на
        # introspection-вызове читается реестром как несовместимость протокола.
        return {"jsonrpc": "2.0", "id": rid, "result": {"resources": []}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"prompts": []}}
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
