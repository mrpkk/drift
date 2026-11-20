#!/usr/bin/env python3
"""Проверка оплаты Drift в USDC на Base — по транзакции, без ключей и без доверия форме.

Зачем: покупатель присылает хеш перевода. Мы читаем квитанцию через публичный RPC
Base и смотрим **событие Transfer** контракта USDC: кому, сколько и по какому токену.
Сумма из формы или из письма не считается доказательством — считается только лог.

    python3 scripts/check_usdc_payment.py --tx 0x… [--min 79] [--address 0x…]

Адрес получателя берётся из ~/.env (`NIDHIPATI_PAYOUT_ADDRESS`, затем `X402_PAY_TO`).
Разбор квитанции — чистая функция: `scan_receipt()` тестируется без сети.

Что скрипт НЕ доказывает: что перевод не вернут, что отправитель — покупатель,
и что сумма окончательная (если покупатель отправил двумя переводами — звать дважды).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path

# Публичные RPC Base. Один адрес — одна точка отказа: 20.11.2026 mainnet.base.org
# отдал 403 на Python-User-Agent, и проверка оплаты «сломалась» без причины.
RPC_URLS = [
    os.environ.get("BASE_RPC_URL", "https://mainnet.base.org"),
    "https://base-rpc.publicnode.com",
    "https://1rpc.io/base",
]
USER_AGENT = "drift-usdc-check/1.0 (+https://rta.lat/drift-offer/)"
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
DECIMALS = 6
TRANSFER_SIG = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
ENV_KEYS = ("NIDHIPATI_PAYOUT_ADDRESS", "X402_PAY_TO", "WALLET_BINANCE_USDC_BASE")


def payout_address(env_path: Path | None = None) -> str:
    path = env_path or (Path.home() / ".env")
    try:
        text = path.read_text()
    except OSError:
        return ""
    values: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Z0-9_]+)=[\"']?([^\"'\n]*)", line)
        if m:
            values[m.group(1)] = m.group(2).strip()
    for key in ENV_KEYS:
        if ADDR_RE.match(values.get(key, "")):
            return values[key]
    return ""


def _topic_addr(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def scan_receipt(receipt: dict, to_address: str, token: str = USDC_BASE,
                 min_amount: float = 0.0) -> dict:
    """Разбирает квитанцию: наш ли токен, наш ли получатель, хватает ли суммы."""
    to_low = to_address.lower()
    token_low = token.lower()
    transfers = []
    for log in receipt.get("logs") or []:
        if str(log.get("address", "")).lower() != token_low:
            continue
        topics = log.get("topics") or []
        if len(topics) < 3 or str(topics[0]).lower() != TRANSFER_SIG:
            continue
        if _topic_addr(str(topics[2])) != to_low:
            continue
        data = str(log.get("data") or "0x")
        try:
            raw = int(data, 16) if data not in ("", "0x") else 0
        except ValueError:
            continue
        transfers.append({"from": _topic_addr(str(topics[1])),
                          "amount": raw / 10 ** DECIMALS, "raw": raw})
    total = round(sum(t["amount"] for t in transfers), 6)
    ok = bool(transfers) and total >= float(min_amount)
    return {
        "ok": ok,
        "to": to_address,
        "token": token,
        "transfers": transfers,
        "total": total,
        "min_amount": float(min_amount),
        "reason": ("paid" if ok else
                   "no_transfer_to_this_address" if not transfers else
                   f"amount_below_min: {total} < {min_amount}"),
        "status": receipt.get("status"),
        "tx_hash": receipt.get("transactionHash"),
    }


def _rpc_once(url: str, method: str, params: list) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,      # без него часть публичных RPC отвечает 403
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        payload = json.loads(r.read().decode())
    if "error" in payload:
        raise RuntimeError(payload["error"].get("message") or "rpc error")
    return payload.get("result") or {}


class RpcUnavailable(RuntimeError):
    """Ни один RPC не ответил — это не «нет перевода», это «не проверили»."""


def _rpc(method: str, params: list) -> dict:
    """Пробуем по очереди: 403/429 на одном адресе не должны врать про оплату."""
    last: str = ""
    for url in RPC_URLS:
        try:
            return _rpc_once(url, method, params)
        except Exception as exc:      # сеть, 4xx, битый JSON — следующий адрес
            last = f"{type(exc).__name__}: {exc}"
    raise RpcUnavailable(last or "no rpc url")


def usdc_balance(address: str) -> float:
    """Текущий баланс USDC адреса — «пришло ли вообще что-нибудь»."""
    if not ADDR_RE.match(address):
        raise ValueError("нужен адрес 0x… из 40 hex")
    data = "0x70a08231" + "0" * 24 + address[2:].lower()
    raw = _rpc("eth_call", [{"to": USDC_BASE, "data": data}, "latest"])
    try:
        return int(str(raw), 16) / 10 ** DECIMALS
    except ValueError:
        return 0.0


def check_tx(tx_hash: str, address: str, min_amount: float = 0.0) -> dict:
    receipt = _rpc("eth_getTransactionReceipt", [tx_hash])
    if not receipt:
        return {"ok": False, "reason": "transaction_not_found", "tx_hash": tx_hash}
    if receipt.get("status") not in ("0x1", 1, None):
        return {"ok": False, "reason": "transaction_failed", "tx_hash": tx_hash,
                "status": receipt.get("status")}
    return scan_receipt(receipt, address, min_amount=min_amount)


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверить оплату Drift в USDC на Base")
    ap.add_argument("--tx", default="", help="хеш транзакции 0x… (не нужен вместе с --balance)")
    ap.add_argument("--min", type=float, default=79.0, help="минимальная сумма (USDC), по умолчанию 79")
    ap.add_argument("--address", default="", help="адрес получателя (по умолчанию из ~/.env)")
    ap.add_argument("--balance", action="store_true", help="показать текущий баланс USDC адреса")
    a = ap.parse_args()
    if not a.balance and not a.tx:
        ap.error("нужен --tx или --balance")
    address = a.address or payout_address()
    if not ADDR_RE.match(address):
        print(json.dumps({"ok": False, "reason": "no_recipient_address"}, ensure_ascii=False))
        return 2
    if a.balance:
        try:
            print(json.dumps({"address": address, "usdc_balance": usdc_balance(address)},
                             ensure_ascii=False, indent=1))
            return 0
        except Exception as exc:
            print(json.dumps({"ok": False, "reason": f"rpc_unavailable: {type(exc).__name__}"},
                             ensure_ascii=False))
            return 3
    try:
        result = check_tx(a.tx, address, min_amount=a.min)
    except Exception as exc:  # RPC/сеть — наружу только текст ошибки
        print(json.dumps({"ok": False, "reason": f"rpc_error: {type(exc).__name__}"}, ensure_ascii=False))
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
