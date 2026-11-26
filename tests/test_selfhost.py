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
    assert service["ports"] == ["127.0.0.1:${DRIFT_PORT:-8095}:8095"]
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
    """Приложение не должно работать под root — но способ изменился 26.11.2026.

    `USER drift` не позволял отдать том состояния пользователю 10001 (том монтируется root:root).
    Теперь контейнер стартует root'ом на один шаг и сразу сбрасывает привилегии в entrypoint.
    Проверяем именно результат: приложение не под root.
    """
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    drops = bool(re.search(r"^USER (?!root)\w+", dockerfile, re.MULTILINE))
    entrypoint = "setpriv --reuid=10001" in (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    assert drops or entrypoint, "приложение осталось бы под root"
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


def test_dockerfile_copies_every_local_module():
    """Образ собирался, а контейнер падал: subscription.py не было в COPY.

    22.11.2026: `docker compose up` → `ModuleNotFoundError: No module named
    'subscription'`. Тест сверяет импорты с тем, что реально копируется в образ.
    """
    import re

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8").replace("\\\n", " ")
    copy_block = " ".join(re.findall(r"^COPY (.+?) \./", dockerfile, re.MULTILINE))
    local_modules = {p.stem for p in ROOT.glob("*.py") if not p.name.startswith("test_")}
    imports = set()
    for name in local_modules:
        text = (ROOT / f"{name}.py").read_text(encoding="utf-8")
        imports |= set(re.findall(r"^(?:import|from) ([a-z_][a-z0-9_]*)", text, re.MULTILINE))
    needed = (imports & local_modules) - {"drift", "mcp_server", "http_server",
                                          "ratelimit", "x402_gate", "subscription"}
    missing = [m for m in sorted(local_modules) if m not in copy_block
               and m in (imports | {"subscription"})]
    assert not missing, f"Dockerfile не копирует: {missing} (нужно для {sorted(needed)})"


def test_compose_port_can_be_overridden():
    """Порт занят живым сервисом — проверять контейнер надо на другом порту."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    port = compose["services"]["drift"]["ports"][0]
    assert "${DRIFT_PORT:-8095}" in port


def test_port_override_is_documented_and_used(compose):
    """Порт занят — обычная ситуация; DRIFT_PORT должен быть и в compose, и в README."""
    import re
    text = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"DRIFT_PORT", text), "compose должен читать DRIFT_PORT"
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "DRIFT_PORT=18095 docker compose up -d" in readme
    assert "curl -s http://127.0.0.1:8095/health" in readme or "/health" in readme


def test_readme_lists_healthy_check_command():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "docker compose ps" in readme
    assert "healthy" in readme.lower()


def test_documented_state_is_exactly_what_the_code_persists(compose):
    """Том не должен обещать больше, чем хранит: в коде пишется только payments.json."""
    src = (ROOT / "http_server.py").read_text(encoding="utf-8")
    assert '"payments.json"' in src
    assert "state_dir" in src
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "payments.json" in readme
    assert "reset on restart" in readme, "обнуление счётчиков должно быть названо, а не скрыто"
    raw = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "Счётчики бесплатной квоты и сессии живут в памяти" in raw


def test_compose_config_is_valid():
    """`docker compose config -q` — единственный способ узнать, что файл не сломан."""
    import shutil
    import subprocess

    if not shutil.which("docker"):
        pytest.skip("docker не установлен")
    proc = subprocess.run(["docker", "compose", "config", "-q"], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-300:]


def test_entrypoint_fixes_volume_then_drops_privileges():
    """Том принадлежит root — приложение должно получить его, но не работать под root."""
    ep = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    assert "setpriv" in ep and "--reuid=10001" in ep, "привилегии должны сбрасываться"
    assert "chown -R 10001:10001" in ep, "каталог состояния передаётся пользователю 10001"
    assert "DRIFT_STATE_DIR" in ep
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["/app/entrypoint.sh"]' in dockerfile
    import re as _re
    assert not _re.search(r"^USER drift", dockerfile, _re.MULTILINE), "root нужен только на шаг chown"
    assert "COPY entrypoint.sh" in dockerfile


def test_payment_state_survives_and_reports_unwritable_path(tmp_path, capsys):
    """Журнал платежей: пишется и читается; при недоступном пути говорит об этом, а не молчит."""
    import sys as _sys

    _sys.path.insert(0, str(ROOT))
    from x402_gate import PaymentConfig, X402Gate

    state = tmp_path / "a" / "payments.json"
    cfg = PaymentConfig(pay_to="0x" + "ab" * 20, state_path=str(state))
    gate = X402Gate(cfg)
    gate._issued["nonce-1"] = {"amount": 5000}
    gate._save_state(force=True)
    assert state.is_file(), "журнал не создан"
    again = X402Gate(cfg)
    assert "nonce-1" in again._issued, "журнал не прочитан после перезапуска"

    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    bad = X402Gate(PaymentConfig(pay_to="0x" + "ab" * 20,
                                 state_path=str(ro / "sub" / "payments.json")))
    bad._save_state(force=True)
    assert "не сохранено" in capsys.readouterr().out


def test_x402_network_aliases_do_not_break_v1():
    """`base` в документации и `base-sepolia` у facilitator'а — не одно и то же (26.11.2026)."""
    import sys as _sys

    _sys.path.insert(0, str(ROOT))
    from x402_gate import PaymentConfig

    assert PaymentConfig(pay_to="0x" + "ab" * 20, network="base-sepolia").network == "base-sepolia"
    # v2 + mainnet требует своего facilitator'а (встроенный обслуживает только сеполню),
    # поэтому проверяем именно нормализацию имени, а не полную валидность конфигурации.
    from x402_gate import NETWORK_ALIASES
    assert NETWORK_ALIASES["base"] == "eip155:8453"
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'DRIFT_X402_NETWORK:-base-sepolia' in compose, "дефолт должен работать с встроенным facilitator"
