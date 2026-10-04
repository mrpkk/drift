"""HTTP-транспорт для MCP-сервера drift. Только стандартная библиотека.

    python3 http_server.py            # stdio (по умолчанию)
    python3 http_server.py --http 8080  # streamable-http для листинга
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

from mcp_server import respond

MCP_PATH = "/mcp"
__version__ = "0.1.0"
HEALTH_PATH = "/health"


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
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
        n = int(self.headers.get("Content-Length") or 0)
        out = respond(self.rfile.read(n))
        if out is None:
            self._send(202, b"")
            return
        status, body = out
        self._send(status, body)

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
