"""Подписка drift: токен, срок и снятие дневного потолка.

Проверяем ровно то, из-за чего подписка может стать дырой в квоте:
токен подделывается, живёт дольше оплаченного, или не снимает лимит вовсе.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import http_server
import subscription


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setenv(subscription.SECRET_ENV, "test-secret")
    return "test-secret"


def test_token_roundtrip(secret):
    token = subscription.issue(now=1_000_000, subject="payer-1")
    payload = subscription.verify(token, now=1_000_000)
    assert payload is not None
    assert payload["plan"] == subscription.PLAN
    assert payload["expires_at"] == 1_000_000 + 30 * 86400


def test_expired_token_is_rejected(secret):
    token = subscription.issue(now=1_000_000)
    assert subscription.verify(token, now=1_000_000 + 30 * 86400 + 1) is None


def test_tampered_payload_is_rejected(secret):
    token = subscription.issue(now=1_000_000)
    body, _, signature = token.partition(".")
    # Продлеваем срок, оставляя старую подпись.
    forged = body[:-2] + "AA" + "." + signature
    assert subscription.verify(forged, now=1_000_000) is None


def test_token_from_another_secret_is_rejected(secret, monkeypatch):
    token = subscription.issue(now=1_000_000)
    monkeypatch.setenv(subscription.SECRET_ENV, "other-secret")
    assert subscription.verify(token, now=1_000_000) is None


def test_without_secret_nothing_is_issued_or_verified(monkeypatch):
    monkeypatch.delenv(subscription.SECRET_ENV, raising=False)
    assert subscription.enabled() is False
    assert subscription.verify("anything.at-all") is None
    with pytest.raises(RuntimeError):
        subscription.issue()


@pytest.fixture
def server(monkeypatch, tmp_path):
    """Сервер с жёстким лимитом в одну бесплатную проверку за сутки."""
    monkeypatch.setenv(subscription.SECRET_ENV, "test-secret")
    monkeypatch.setattr(http_server, "FREE_TIER_PER_DAY", 1)
    monkeypatch.setattr(http_server, "LIMITER",
                        http_server.RateLimiter(limit=1, window_seconds=86400))
    monkeypatch.setattr(http_server, "GATE", None)
    monkeypatch.setattr(http_server, "SUB_GATE", None)
    httpd = HTTPServer(("127.0.0.1", 0), http_server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        monkeypatch.setattr(http_server, "LIMITER", None)


def _call(base: str, headers: dict | None = None) -> int:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "explain_rules", "arguments": {}}}).encode()
    req = urllib.request.Request(base + "/mcp", data=body,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_subscription_lifts_the_daily_cap(server):
    assert _call(server) in (200, 202)          # первая бесплатная
    assert _call(server) == 429                 # лимит исчерпан
    token = subscription.issue()
    assert _call(server, {subscription.HEADER: token}) in (200, 202)


def test_forged_token_does_not_lift_the_cap(server):
    assert _call(server) in (200, 202)
    _, _, signature = subscription.issue().partition(".")
    assert _call(server, {subscription.HEADER: "Zm9yZ2Vk." + signature}) == 429


def test_subscribe_returns_402_without_payment(server):
    req = urllib.request.Request(server + "/subscribe", data=b"{}",
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)
    # Без кошелька подписка честно говорит, что не продаётся.
    assert exc.value.code in (402, 503)
