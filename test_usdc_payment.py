"""Проверка оплаты в USDC: разбор квитанции без сети.

Деньги считаются по логу Transfer, а не по сумме из формы. Тесты держат три
случая, которые дороже всего стоят при ошибке: чужой токен, чужой получатель,
сумма ниже порога.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from check_usdc_payment import USDC_BASE, scan_receipt  # noqa: E402

OURS = "0xefCD6886f73064ea9EcbaA4741Ef1309A0885C59"
BUYER = "0x1111111111111111111111111111111111111111"


def _topic(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:].lower()


def _log(to: str = OURS, amount_usdc: float = 79, token: str = USDC_BASE,
         sig: str = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef") -> dict:
    raw = int(round(amount_usdc * 10 ** 6))
    return {"address": token,
            "topics": [sig, _topic(BUYER), _topic(to)],
            "data": hex(raw)}


def _receipt(*logs: dict, status: str = "0x1") -> dict:
    return {"status": status, "transactionHash": "0xabc", "logs": list(logs)}


def test_exact_transfer_is_paid():
    res = scan_receipt(_receipt(_log(amount_usdc=79)), OURS, min_amount=79)
    assert res["ok"] is True, res
    assert res["total"] == 79.0 and res["reason"] == "paid"
    assert res["transfers"][0]["from"] == BUYER.lower()


def test_amount_below_min_is_not_paid():
    res = scan_receipt(_receipt(_log(amount_usdc=78.5)), OURS, min_amount=79)
    assert res["ok"] is False
    assert res["reason"].startswith("amount_below_min")
    assert res["total"] == 78.5


def test_wrong_recipient_is_not_paid():
    other = "0x2222222222222222222222222222222222222222"
    res = scan_receipt(_receipt(_log(to=other, amount_usdc=200)), OURS, min_amount=79)
    assert res["ok"] is False and res["reason"] == "no_transfer_to_this_address"


def test_wrong_token_is_not_paid():
    fake = "0x3333333333333333333333333333333333333333"
    res = scan_receipt(_receipt(_log(token=fake, amount_usdc=500)), OURS, min_amount=79)
    assert res["ok"] is False and res["transfers"] == []


def test_two_transfers_add_up():
    res = scan_receipt(_receipt(_log(amount_usdc=40), _log(amount_usdc=39)), OURS, min_amount=79)
    assert res["ok"] is True and res["total"] == 79.0


def test_other_events_are_ignored():
    approval_sig = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
    res = scan_receipt(_receipt(_log(amount_usdc=0.01, sig=approval_sig)), OURS, min_amount=0.01)
    assert res["ok"] is False and res["transfers"] == []


def test_recipient_case_insensitive():
    lower = OURS.lower()
    res = scan_receipt(_receipt(_log()), lower, min_amount=79)
    assert res["ok"] is True, res
