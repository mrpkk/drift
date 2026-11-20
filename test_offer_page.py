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


def test_offer_has_price_markup_for_search():
    """Без JSON-LD поисковик не покажет цену, а чат-клиент не поймёт, что это услуга."""
    import json as _json
    import re
    html = _html()
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert m, "нет разметки JSON-LD"
    data = _json.loads(m.group(1))
    assert data["@type"] == "Service"
    assert data["offers"]["price"] == "79" and data["offers"]["priceCurrency"] == "USD"
    assert 'property="og:title"' in html and 'property="og:url"' in html


def test_offer_is_in_domain_sitemap_after_publish():
    """В репозитории лежит копия; карту сайта собирает rta.http_api — проверяем исходник."""
    from pathlib import Path
    api = Path("/media/Sruti/iamthat/work/sales/rta/rta/http_api.py")
    if not api.exists():
        import pytest
        pytest.skip("нет модуля rta.http_api на этой машине")
    text = api.read_text(encoding="utf-8")
    assert "/drift-offer/" in text, "оффер не попал в карту сайта"


def test_offer_is_named_in_domain_llms_txt():
    """llms.txt читают AI-клиенты: оффер без записи там не существует для модели."""
    from pathlib import Path
    api = Path("/media/Sruti/iamthat/work/sales/rta/rta/http_api.py")
    if not api.exists():
        import pytest
        pytest.skip("нет модуля rta.http_api на этой машине")
    text = api.read_text(encoding="utf-8")
    assert "/drift-offer/" in text, "оффер не назван в llms.txt домена"
    assert "$79" in text, "цена не названа"
