"""drift — непрерывная проверка действий агента против подписанного мандата.

Проблема, которую это закрывает
------------------------------
AP2 и подобные протоколы проверяют мандат ОДИН РАЗ — в момент подписи. Дальше
агент действует сам, и между подписью и последствием нет ничего, что удержало
бы его в рамках. Проверка заканчивается, авторитет — нет.

drift продолжает проверку: каждый поступленный факт действия сверяется с
полномочием, а расхождение фиксируется как доказательство, а не как лог.

Модель
-----
Mandate   — что разрешено: получатели, лимиты, суммы, срок, аудитория
Action    — что агент фактически сделал или собирается
Verdict   — ALLOW / DENY с указанием, какое именно правило сработало
Ledger    — append-only цепочка решений: сама она является доказательством

Принцип: правило проверяется ДО действия, а не после. Deny — это дефолт.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(frozen=True)
class Mandate:
    """Полномочие. Неизменяемый: любая правка — это новый мандат."""
    payee_id: str
    audience: str
    max_amount_minor: int
    currency: str
    valid_from: int
    valid_until: int
    allowed_intents: tuple[str, ...] = ()

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Mandate":
        return Mandate(
            payee_id=d["payee_id"],
            audience=d["audience"],
            max_amount_minor=int(d["max_amount_minor"]),
            currency=d["currency"],
            valid_from=int(d["valid_from"]),
            valid_until=int(d["valid_until"]),
            allowed_intents=tuple(d.get("allowed_intents", ())),
        )


@dataclass(frozen=True)
class Action:
    """Поступление от агента. Ничего не доверяем: всё сверяем с мандатом."""
    intent: str
    payee_id: str
    audience: str
    amount_minor: int
    currency: str
    at: int

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Action":
        return Action(
            intent=d["intent"],
            payee_id=d["payee_id"],
            audience=d["audience"],
            amount_minor=int(d["amount_minor"]),
            currency=d["currency"],
            at=int(d["at"]),
        )


RULES = ("payee", "audience", "amount", "currency", "window", "intent")


class Ledger:
    """Append-only. Порядок решений — самостоятельный носитель доказательства."""

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    def append(self, row: dict[str, Any]) -> None:
        self._rows.append(row)

    @property
    def rows(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def denied(self) -> list[dict[str, Any]]:
        return [r for r in self._rows if r["verdict"] == "DENY"]

    def fingerprint(self) -> str:
        """Цепочка связана: изменение любой записи ломает хеш от неё."""
        import hashlib

        h = hashlib.sha256()
        for r in self._rows:
            h.update(json.dumps(r, sort_keys=True, separators=(",", ":")).encode())
        return h.hexdigest()[:16]


def check(m: Mandate, a: Action) -> tuple[str, str]:
    """Возвращает (вердикт, правило). Порядок правил фиксирован и значим.

    Порядок выбран так, чтобы самая частая причина отказа стояла первой:
    сумма превышена — самая вероятная форма дрейфа.
    """
    if a.amount_minor > m.max_amount_minor:
        return "DENY", "amount"
    if a.payee_id != m.payee_id:
        return "DENY", "payee"
    if a.audience != m.audience:
        return "DENY", "audience"
    if a.currency != m.currency:
        return "DENY", "currency"
    if not (m.valid_from <= a.at <= m.valid_until):
        return "DENY", "window"
    if m.allowed_intents and a.intent not in m.allowed_intents:
        return "DENY", "intent"
    return "ALLOW", "-"


class Drift:
    """Держатель мандата. Ничего не знает о том, доверять ли агенту."""

    def __init__(self, mandate: Mandate, now: int | None = None) -> None:
        self.m = mandate
        self.ledger = Ledger()
        self.now = now

    def submit(self, action: Action) -> tuple[str, str]:
        verdict, rule = check(self.m, action)
        self.ledger.append({
            "at": action.at,
            "intent": action.intent,
            "verdict": verdict,
            "rule": rule,
            "payee_id": action.payee_id,
            "audience": action.audience,
            "amount_minor": action.amount_minor,
            "currency": action.currency,
        })
        return verdict, rule

    def report(self) -> dict[str, Any]:
        rows = self.ledger.rows
        return {
            "submitted": len(rows),
            "allowed": sum(1 for r in rows if r["verdict"] == "ALLOW"),
            "denied": len(self.ledger.denied()),
            "breach_by_rule": self._by_rule(),
            "fingerprint": self.ledger.fingerprint(),
        }

    def _by_rule(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.ledger.denied():
            out[r["rule"]] = out.get(r["rule"], 0) + 1
        return dict(sorted(out.items()))
