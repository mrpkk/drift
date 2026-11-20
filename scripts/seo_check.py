#!/usr/bin/env python3
"""Витрина drift: находима ли она и понятна ли поиску/чатам.

    python3 scripts/seo_check.py            # живые проверки, код 1 при провале

Почему это важнее правок текста: пока страницу не видно в карте сайта и у неё
нет разметки с ценой, трафик может прийти только по прямой ссылке — то есть от
человека, который уже знает адрес. Проверяем ровно то, что от нас зависит.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

OFFER = "https://rta.lat/drift-offer/"
SITEMAP = "https://rta.lat/sitemap.xml"
AGENT = "drift-seo-check/1.0"


def fetch(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.status, r.read().decode("utf-8", "ignore")


def check() -> list[tuple[bool, str]]:
    rows: list[tuple[bool, str]] = []

    status, html = fetch(OFFER)
    rows.append((status == 200, f"оффер отвечает {status}"))

    rows.append(("<title>" in html and "79" in html.split("</title>")[0],
                 "в <title> есть цена — иначе сниппет в поиске пустой"))

    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    schema_ok, schema_note = False, "разметки JSON-LD нет"
    if m:
        try:
            data = json.loads(m.group(1))
            offer = data.get("offers", {})
            schema_ok = (offer.get("price") == "79" and offer.get("priceCurrency") == "USD")
            schema_note = f"JSON-LD {data.get('@type')}, цена {offer.get('price')} {offer.get('priceCurrency')}"
        except ValueError:
            schema_note = "JSON-LD не разбирается как JSON"
    rows.append((schema_ok, schema_note))

    status, xml = fetch(SITEMAP)
    rows.append((status == 200 and "/drift-offer/" in xml,
                 f"карта сайта содержит /drift-offer/ ({status})"))

    status, robots = fetch("https://rta.lat/robots.txt")
    rows.append((status == 200 and "sitemap.xml" in robots.lower(),
                 "robots.txt указывает на карту сайта"))

    # llms.txt — то, из чего AI-клиенты берут описание домена. Если оффера там нет,
    # модель о нём не расскажет, сколько бы мы ни правили страницу.
    status, llms = fetch("https://rta.lat/llms.txt")
    rows.append((status == 200 and "/drift-offer/" in llms and "79" in llms,
                 "llms.txt домена называет оффер и цену"))
    return rows


def main() -> int:
    rows = check()
    for ok, text in rows:
        print(("ok   " if ok else "FAIL ") + text)
    bad = [t for ok, t in rows if not ok]
    print("вердикт:", "витрина находима" if not bad else f"проблем: {len(bad)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
