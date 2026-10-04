"""HTTP-транспорт для MCP-сервера drift. Только стандартная библиотека.

    python3 http_server.py            # stdio (по умолчанию)
    python3 http_server.py --http 8080  # streamable-http для листинга
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

from mcp_server import respond
from ratelimit import DAY_SECONDS, RateLimiter, client_key

MCP_PATH = "/mcp"
FREE_TIER_PER_DAY = int(os.environ.get("DRIFT_FREE_TIER_PER_DAY", "100"))
DISABLE_RATE_LIMIT_ENV = "DRIFT_NO_RATE_LIMIT"  # значение "1" отключает квоту
LIMITER: RateLimiter | None = None  # создаётся в main(); None = без лимита
__version__ = "0.1.0"
HEALTH_PATH = "/health"


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
        if LIMITER is not None:
            decision = LIMITER.check(client_key(self.headers, self.client_address))
            if not decision.allowed:
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
        n = int(self.headers.get("Content-Length") or 0)
        out = respond(self.rfile.read(n))
        if out is None:
            self._send(202, b"", decision.headers() if LIMITER is not None else None)
            return
        status, body = out
        self._send(status, body, decision.headers() if LIMITER is not None else None)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == HEALTH_PATH:
            self._send(200, json.dumps(
                {"status": "ok", "service": "drift", "version": __version__},
                ensure_ascii=False).encode())
            return
        if self.path.rstrip("/") in (MCP_PATH, ""):
            self._send(200, json.dumps(
                {"server": "drift", "transport": "streamable-http", "path": MCP_PATH},
                ensure_ascii=False).encode())
            return
        self._send(404, b'{"error":"not found"}')

    def log_message(self, *_a: Any) -> None:  # тихо
        pass


def main() -> None:
    global LIMITER
    if os.environ.get(DISABLE_RATE_LIMIT_ENV) != "1":
        LIMITER = RateLimiter(limit=FREE_TIER_PER_DAY, window_seconds=DAY_SECONDS)
    args = sys.argv[1:]
    if "--http" in args:
        port = int(args[args.index("--http") + 1])
        HTTPServer(("127.0.0.1", port), Handler).serve_forever()
        return
    for line in sys.stdin:
        out = respond(line.encode())
        if out:
            sys.stdout.write(out[1].decode() + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
