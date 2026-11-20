"""Оффер Drift: адрес из ~/.env, оплата картой и USDC на одной странице.

Страница раздаётся из каталога лендингов, а версия живёт в `offer/index.html`.
Тест держит три вещи: продукт в кассе тот, адрес совпадает с окружением,
и на странице нет обещания «адрес выдаётся после заявки» рядом с самим адресом.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from publish_offer import payout_address, render  # noqa: E402

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "offer" / "index.html"
ADDR_RE = re.compile(r"0x[a-fA-F0-9]{40}")


def _html() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_page_has_card_and_usdc_paths():
    html = _html()
    assert "pay/card?product=drift-kill-switch" in html
    assert "79 USDC" in html and "Base" in html
    assert "Хеш" in html or "хеш" in html, "нет просьбы прислать хеш после перевода"


def test_no_contradiction_about_address():
    html = _html()
    assert "адрес выдаётся после заявки" not in html, \
        "страница обещает выдать адрес, а адрес уже на ней"
    assert "адрес ниже" in html


def test_single_address_on_page():
    found = set(ADDR_RE.findall(_html()))
    assert len(found) == 1, found


def test_render_from_env_rewrites_address():
    env_address = payout_address()
    if not env_address:
        pytest.skip("нет ~/.env с адресом получателя")
    html = render(_html(), env_address)
    assert set(ADDR_RE.findall(html)) == {env_address}
    assert "product=drift-kill-switch" in html


def test_render_rejects_bad_address():
    with pytest.raises(ValueError):
        render(_html(), "0x123")
