"""HTTP-транспорт для MCP-сервера drift. Только стандартная библиотека.

    python3 http_server.py            # stdio (по умолчанию)
    python3 http_server.py --http 8080  # streamable-http для листинга
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

from mcp_server import TOOLS, respond
from ratelimit import DAY_SECONDS, RateLimiter, client_key
from x402_gate import (
    HEADER_PAYMENT,
    PAYMENT_HEADERS,
    FacilitatorUnreachable,
    PaymentConfig,
    X402Gate,
    challenge_headers,
)

MCP_PATH = "/mcp"
FREE_TIER_PER_DAY = int(os.environ.get("DRIFT_FREE_TIER_PER_DAY", "100"))
DISABLE_RATE_LIMIT_ENV = "DRIFT_NO_RATE_LIMIT"  # значение "1" отключает квоту
X402_ENABLED_ENV = "DRIFT_X402_ENABLED"
X402_PAY_TO_ENV = "DRIFT_X402_PAY_TO"
X402_NETWORK_ENV = "DRIFT_X402_NETWORK"
X402_FACILITATOR_ENV = "DRIFT_X402_FACILITATOR"
# Куда класть выданные и потраченные nonce. Без этого перезапуск
# возвращал потраченный nonce к жизни: один платёж — две услуги.
X402_STATE_DIR_ENV = "DRIFT_STATE_DIR"
X402_VERSION_ENV = "DRIFT_X402_VERSION"
X402_RESOURCE_ENV = "DRIFT_X402_RESOURCE"
DEFAULT_HOST = "127.0.0.1"  # сетевой доступ — только через явный --host


# Методы MCP, не тратящие квоту: обнаружение, а не работа.
FREE_METHODS = frozenset({
    "initialize", "tools/list", "resources/list", "prompts/list",
    "ping", "notifications/initialized", "notifications/cancelled",
})


def _request_tool(raw: bytes) -> dict | None:
    """Инструмент, который вызывает клиент, — для декларации Bazaar.

    Каталог PayAI ведёт запись на пару ``(resource, toolName)``; без имени
    инструмента объявление не построить, поэтому 402 уносит его с собой.
    """
    try:
        msg = json.loads(raw or b"{}")
    except ValueError:
        return None
    params = msg.get("params") if isinstance(msg, dict) else None
    name = params.get("name") if isinstance(params, dict) else None
    if not isinstance(name, str):
        return None
    return next((tool for tool in TOOLS if tool.get("name") == name), None)


def _is_free_method(raw: bytes) -> bool:
    """Бесплатна ли операция. Не разобрали — считаем платной.

    Ошибка в сторону платности безопаснее: бесплатным по ошибке станет
    вызов работы, и квота перестанет что-либо ограничивать.
    """
    try:
        payload = json.loads(raw.decode("utf-8", "ignore") or "{}")
    except (ValueError, TypeError):
        return False
    if isinstance(payload, list):
        # Пустой список — не «всё бесплатное»: all() на пустом даёт True,
        # и пустой батч обходил бы квоту. Требуем непустой разбор.
        if not payload:
            return False
        return all(isinstance(p, dict) and p.get("method") in FREE_METHODS
                   for p in payload)
    return isinstance(payload, dict) and payload.get("method") in FREE_METHODS


def log(message: str) -> None:
    """Единственная строка в stdout: systemd должен видеть, что сервер поднялся.

    Молчащий при старте процесс невозможно отличить от зависшего, поэтому адрес
    и порт печатаются явно.
    """
    sys.stderr.write(f"drift: {message}\n")
    sys.stderr.flush()
GATE: X402Gate | None = None  # создаётся в main(); None = только бесплатный тариф
LIMITER: RateLimiter | None = None  # создаётся в main(); None = без лимита
__version__ = "0.1.0"
HEALTH_PATH = "/health"


LANDING = """<!doctype html><html lang="en"><head><meta charset=utf-8>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>drift — mandate enforcement for AI agents (MCP)</title>
<meta name="description" content="An MCP server that decides whether an agent was allowed to do what it did. Blocks the action instead of returning a flag nobody checks.">
<link rel="canonical" href="https://drift.rta.lat/">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"SoftwareApplication",
"name":"drift","applicationCategory":"DeveloperApplication",
"operatingSystem":"Any",
"description":"MCP server enforcing mandates for AI agents: it blocks a call that was not permitted rather than returning a verdict the caller may forget to read.",
"url":"https://drift.rta.lat/",
"offers":{"@type":"Offer","price":"0","priceCurrency":"USD"}}
</script>
<style>
:root{--bg:#0b0d12;--fg:#e8ecf4;--dim:#98a2b3;--line:#1e2430;--acc:#58a6ff;--ok:#3fb950}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,sans-serif;
 max-width:760px;margin:60px auto;padding:0 24px}
h1{font-size:28px;line-height:1.25;margin:0 0 8px}
h2{font-size:18px;margin:32px 0 8px}
p{color:var(--dim);margin:0 0 14px}
code,pre{background:#141821;border-radius:6px;padding:2px 7px;
 font-family:ui-monospace,SFMono-Regular,monospace;font-size:14px}
pre{padding:14px;overflow-x:auto;margin:0 0 14px}
a{color:var(--acc)}
.card{border:1px solid var(--line);border-radius:10px;padding:16px 18px;
 margin:0 0 12px}
.tag{color:var(--ok);font-size:13px;letter-spacing:.04em;text-transform:uppercase}
</style></head><body>
<main>
<h1>drift — did the agent have the right to do that?</h1>
<p>An MCP server that enforces <strong>mandates</strong> for AI agents.
It blocks the call that was not permitted, instead of returning a
verdict the caller may forget to read.</p>

<div class="card">
<span class="tag">The difference</span>
<p style="margin:8px 0 0">Most checks return <code>verdict: ALLOW</code>
and leave enforcement to the caller. With <code>enforce=true</code>, drift
stops the flow itself.</p>
</div>

<h2>Connect</h2>
<pre><code>https://drift.rta.lat/mcp</code></pre>

<h2>Price</h2>
<div class="card">
<p style="margin:0">100 free checks per day. Then 0.005 USDC per call on
Base mainnet over x402. No account, no card, no SDK.</p>
</div>

<h2>Try before you install</h2>
<p>The server runs against your own endpoint. Standard library only,
Python 3.11+.</p>
<p><a href="https://indra.rta.lat/">See it in the Indra showcase</a></p>
</main></body></html>"""


def _base_url() -> str:
    """Публичный адрес: в sitemap обязаны быть абсолютные URL."""
    import os
    return os.environ.get("DRIFT_BASE_URL",
                          "https://drift.rta.lat").rstrip("/")


def _robots() -> str:
    """Краулеров пускаем: индексация витрины — это трафик."""
    return ("User-agent: *\n"
            "Allow: /\n"
            "Disallow: /mcp\n"
            f"Sitemap: {_base_url()}/sitemap.xml\n")


def _sitemap() -> str:
    """Карта сайта: документы, а не endpoints."""
    base = _base_url()
    urls = "".join(
        f"<url><loc>{base}{page}</loc></url>" for page in ("", "/health"))
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{urls}</urlset>")

class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == HEALTH_PATH:
            self._send(200, json.dumps(
                {"status": "ok", "service": "drift", "version": __version__},
                ensure_ascii=False).encode())
            return
        if self.path.rstrip("/") not in (MCP_PATH, ""):
            self._send(404, b'{"error":"not found"}')
            return
        # Free tier: quota is charged per request, before any parsing, so an exhausted
        # caller cannot make the server do work. Anonymous callers need no key.
        # Оба имени заголовка: v1 — X-PAYMENT, v2 — PAYMENT-SIGNATURE.
        # Сервис выставляет v1, но SUPPORTED_COMBINATIONS уже держит
        # пары (2, …), и плательщик по v2 иначе остался бы неуслышанным.
        payment = next((self.headers.get(h) for h in PAYMENT_HEADERS
                        if self.headers.get(h)), None)
        if payment:
            status, body, extra = self._settle(payment)
            self._send(status, body, extra)
            return
        # Тело читаем ДО проверки квоты: нужно знать метод. Рукопожатие
        # (initialize) и перечисление инструментов (tools/list) лимит не
        # тратят. Иначе каталог, проверяющий нас ежечасно, оплачивает
        # квотой право узнать, что мы существуем, и при исчерпании
        # помечает сервер нерабочим. Glama получила HTTP 402 именно так и
        # опустила нас ниже здоровых серверов в выдаче.
        #
        # Платной остаётся работа: tools/call. Обнаружение бесплатно.
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        if _is_free_method(raw):
            out = respond(raw)
            if out is None:
                self._send(202, b"")
                return
            status, body = out
            self._send(status, body)
            return

        if LIMITER is not None:
            decision = LIMITER.check(client_key(self.headers, self.client_address))
            if not decision.allowed:
                if GATE is not None:
                    nonce = GATE.issue_nonce()
                    self._send(402, json.dumps({
                        "error": "payment_required",
                        "free_tier_exhausted": True,
                        "limit": decision.limit,
                        "message": "Free tier allows "
                                   f"{decision.limit} checks per day. Pay "
                                   f"{GATE.config.price} USDC on "
                                   f"{GATE.config.network} and retry with an "
                                   f"{HEADER_PAYMENT} header.",
                    }, ensure_ascii=False).encode(),
                        {**challenge_headers(GATE.config, nonce,
                                             _request_tool(raw)),
                         "Retry-After": str(decision.retry_after)})
                    return
                self._send(429, json.dumps({
                    "error": "quota_exceeded",
                    "limit": decision.limit,
                    "reset_at": decision.reset_at,
                    "retry_after": decision.retry_after,
                    "message": "Free tier allows "
                               f"{decision.limit} checks per day. Paid tiers are "
                               "coming; the mandate check itself is unchanged.",
                }, ensure_ascii=False).encode(), decision.headers())
                return
        out = respond(raw)
        if out is None:
            self._send(202, b"", decision.headers() if LIMITER is not None else None)
            return
        status, body = out
        self._send(status, body, decision.headers() if LIMITER is not None else None)

    def _settle(self, payment: str) -> tuple[int, bytes, dict[str, str] | None]:
        """Проверить оплату и, если она настоящая, отдать её квитанцию."""
        if GATE is None:
            return 501, json.dumps({
                "error": "payments_disabled",
                "message": "This deployment runs the free tier only.",
            }, ensure_ascii=False).encode(), None
        try:
            settlement = GATE.verify(payment, GATE.config.resource)
        except FacilitatorUnreachable as exc:
            # Не «оплата отклонена»: мы не смогли проверить. Клиент должен повторить.
            return 503, json.dumps({
                "error": "verification_unavailable",
                "reason": str(exc),
                "message": "The payment could not be verified right now. Retry with "
                           "the same payment; it has not been rejected.",
            }, ensure_ascii=False).encode(), {"Retry-After": "30"}
        except ValueError as exc:
            nonce = GATE.issue_nonce()
            return 402, json.dumps({
                "error": "payment_rejected",
                "reason": str(exc),
            }, ensure_ascii=False).encode(), challenge_headers(GATE.config, nonce)
        return 200, json.dumps({
            "paid": True,
            "amount": str(settlement.amount),
            "network": settlement.network,
            "transaction": settlement.transaction_ref,
        }, ensure_ascii=False).encode(), GATE.settlement_headers(settlement)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == HEALTH_PATH:
            self._send(200, json.dumps(
                {"status": "ok", "service": "drift", "version": __version__},
                ensure_ascii=False).encode())
            return
        if self.path.rstrip("/") == "/.well-known/glama.json":
            from mcp_server import glama_claim
            card = glama_claim()
            if card:
                self._send(200, json.dumps(card).encode("utf-8"),
                           {"X-Content-Type": "application/json; charset=utf-8"})
            else:
                self._send(404, b'{"error":"claim token not configured"}')
            return
        if self.path.rstrip("/") == "/.well-known/mcp/server-card.json":
            from mcp_server import server_card
            self._send(200, json.dumps(server_card()).encode("utf-8"),
                       {"X-Content-Type": "application/json; charset=utf-8"})
            return
        if self.path.rstrip("/") == "/robots.txt":
            self._send(200, _robots().encode("utf-8"),
                       {"X-Content-Type": "text/plain; charset=utf-8"})
            return
        if self.path.rstrip("/") == "/sitemap.xml":
            self._send(200, _sitemap().encode("utf-8"),
                       {"X-Content-Type": "application/xml; charset=utf-8"})
            return
        if self.path.rstrip("/") == "":
            self._send(200, LANDING.encode("utf-8"),
                       {"X-Content-Type": "text/html; charset=utf-8"})
            return
        if self.path.rstrip("/") == MCP_PATH:
            self._send(200, json.dumps(
                {"server": "drift", "transport": "streamable-http", "path": MCP_PATH},
                ensure_ascii=False).encode())
            return
        self._send(404, b'{"error":"not found"}')

    def log_message(self, *_a: Any) -> None:  # тихо
        pass


def main() -> None:
    global LIMITER, GATE
    if os.environ.get(X402_ENABLED_ENV) == "1":
        pay_to = os.environ.get(X402_PAY_TO_ENV, "").strip()
        if not pay_to:
            raise SystemExit(f"{X402_PAY_TO_ENV} must be set to receive payments")
        # Сеть и фасилитатор задаются окружением: зашитый Sepolia означал, что
        # переключить деплой на mainnet можно было только правкой кода.
        kwargs = {"pay_to": pay_to}
        network = os.environ.get(X402_NETWORK_ENV, "").strip()
        if network:
            kwargs["network"] = network
        facilitator = os.environ.get(X402_FACILITATOR_ENV, "").strip()
        if facilitator:
            kwargs["facilitator_url"] = facilitator
        state_dir = os.environ.get(X402_STATE_DIR_ENV, "").strip()
        if state_dir:
            kwargs["state_path"] = str(pathlib.Path(state_dir) / "payments.json")
        version = os.environ.get(X402_VERSION_ENV, "").strip()
        if version:
            kwargs["version"] = int(version)
        resource = os.environ.get(X402_RESOURCE_ENV, "").strip()
        if resource:
            kwargs["resource"] = resource
        GATE = X402Gate(PaymentConfig(**kwargs))
    if os.environ.get(DISABLE_RATE_LIMIT_ENV) != "1":
        LIMITER = RateLimiter(limit=FREE_TIER_PER_DAY, window_seconds=DAY_SECONDS)
    args = sys.argv[1:]
    if "--http" in args:
        port = int(args[args.index("--http") + 1])
        # Хост по умолчанию — петля: сервер не должен слушать в сети без явного
        # разрешения. В контейнере или за туннелем нужен --host 0.0.0.0, иначе
        # проброс порта упирается в петлю и контейнер «работает», не отвечая.
        host = DEFAULT_HOST
        if "--host" in args:
            host = args[args.index("--host") + 1]
        log(f"listening on {host}:{port}")
        HTTPServer((host, port), Handler).serve_forever()
        return
    for line in sys.stdin:
        out = respond(line.encode())
        if out:
            sys.stdout.write(out[1].decode() + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
