"""Self-host: compose, README и живой запуск.

21.11.2026: образ был, а гайда и compose не было — «self-host» держался на чтении
Dockerfile. Тест держит три вещи, которые легко разъедутся: порт в compose, тот же
порт в healthcheck, и наличие самих команд в README.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_compose_maps_the_same_port_the_server_exposes(compose):
    service = compose["services"]["drift"]
    assert service["ports"] == ["127.0.0.1:8095:8095"]
    assert service["restart"] == "unless-stopped"


def test_compose_is_hardened_and_persistent(compose):
    service = compose["services"]["drift"]
    assert service["read_only"] is True
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["volumes"] == ["drift-state:/data"]
    assert "drift-state" in compose["volumes"]
    # состояние должно лежать на томе, а не в корневой ФС
    assert service["environment"]["DRIFT_STATE_DIR"].startswith("/data")


def test_healthcheck_hits_the_same_endpoint(compose):
    test_cmd = " ".join(compose["services"]["drift"]["healthcheck"]["test"])
    assert "/health" in test_cmd
    assert "8095" in test_cmd


def test_paid_rail_is_off_by_default(compose):
    env = compose["services"]["drift"]["environment"]
    assert env["DRIFT_X402_ENABLED"] == "${DRIFT_X402_ENABLED:-0}"
    assert env["DRIFT_X402_PAY_TO"] == "${DRIFT_X402_PAY_TO:-}"


def test_image_is_non_root_with_healthcheck():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^USER (?!root)\w+", dockerfile, re.MULTILINE)
    assert "HEALTHCHECK" in dockerfile and "/health" in dockerfile


def test_readme_has_runnable_selfhost_commands():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Self-host in four commands" in readme
    for needle in ("docker compose up -d --build", "/health", "tools/list",
                   "DRIFT_X402_ENABLED", "drift-state"):
        assert needle in readme, needle


def test_server_starts_and_answers_health():
    """Живой запуск: то, что описано в гайде, действительно поднимается."""
    import socket

    with socket.socket() as sock:      # свободный порт: 8195+ заняты другими сервисами
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "http_server.py", "--http", str(port), "--host", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + 15
        body = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                    assert r.status == 200
                    body = json.loads(r.read().decode("utf-8"))
                    break
            except Exception:  # noqa: BLE001 — сервер ещё поднимается
                time.sleep(0.4)
        assert body is not None, "сервер не ответил /health за 15 секунд"
        assert body.get("ok") in (True, None)

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/mcp", method="POST",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = json.loads(r.read().decode("utf-8"))
        assert payload.get("result", {}).get("tools"), "tools/list пуст"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
