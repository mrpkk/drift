# drift — continuous mandate enforcement for AI agents.
# Standard library only: no dependency installation step, nothing to resolve at build
# time, so the build cannot fail on a network hiccup during a registry scan.
FROM python:3.12-slim

# Non-root: the server needs no write access and no capabilities.
RUN useradd --system --create-home --uid 10001 drift
WORKDIR /app

COPY drift.py mcp_server.py http_server.py ratelimit.py x402_gate.py server.json ./

USER drift
EXPOSE 8095

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8095/health',timeout=4).status==200 else 1)"

# stdio is the default transport for local MCP clients; override to expose HTTP.
CMD ["python3", "http_server.py", "--http", "8095", "--host", "0.0.0.0"]
