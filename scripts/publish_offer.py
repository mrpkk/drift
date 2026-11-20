#!/usr/bin/env python3
"""Публикация оффера Drift: адрес получателя подставляется из ~/.env.

Почему так: страница живёт в `offer/index.html` (версия в репозитории), а
раздаётся из каталога лендингов. Адрес в HTML — единственное поле, ошибка в
котором стоит денег покупателя, поэтому при публикации он перезаписывается
значением из окружения, а не остаётся тем, что кто-то вписал руками месяц назад.

    python3 scripts/publish_offer.py --check    # что изменится
    python3 scripts/publish_offer.py            # опубликовать
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "offer" / "index.html"
TARGET_DIR = Path("/media/Sruti/iamthat/work/sales/landing/drift-offer")
TARGET = TARGET_DIR / "index.html"
ADDR_RE = re.compile(r"0x[a-fA-F0-9]{40}")
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
        if ADDR_RE.fullmatch(values.get(key, "")):
            return values[key]
    return ""


def render(html: str, address: str) -> str:
    """Один адрес на странице: все вхождения 0x…40 заменяются на адрес из окружения."""
    if not ADDR_RE.fullmatch(address or ""):
        raise ValueError("нужен адрес 0x… из 40 hex")
    return ADDR_RE.sub(address, html)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--target", default=str(TARGET))
    a = ap.parse_args()
    address = payout_address()
    if not address:
        print("нет адреса в ~/.env", file=sys.stderr)
        return 2
    html = render(SOURCE.read_text(encoding="utf-8"), address)
    target = Path(a.target)
    if a.check:
        same = target.exists() and target.read_text(encoding="utf-8") == html
        print(f"{'совпадает' if same else 'будет обновлено'}: {target}")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    print(f"опубликовано: {target} (адрес {address[:10]}…{address[-4:]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
