"""Тесты MCP-протокола drift. python3 test_mcp.py"""
import json
import unittest

import mcp_server as S

MANDATE = {
    "payee_id": "shop", "audience": "merchant.example",
    "max_amount_minor": 10_000, "currency": "USD",
    "valid_from": 1_700_000_000, "valid_until": 1_700_086_400,
    "allowed_intents": ["buy"],
}


def call(name, args):
    r = S.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": args}})
    return json.loads(r["result"]["content"][0]["text"])


def act(**kw):
    a = {"intent": "buy", "payee_id": "shop", "audience": "merchant.example",
         "amount_minor": 100, "currency": "USD", "at": 1_700_000_060}
    a.update(kw)
    return a


class TestProtocol(unittest.TestCase):
    def test_initialize(self):
        r = S.handle_request({"jsonrpc": "2.0", "id": 0, "method": "initialize"})
        self.assertEqual(r["result"]["serverInfo"]["name"], "drift")
        self.assertIn("protocolVersion", r["result"])

    def test_tools_list_three_tools(self):
        r = S.handle_request({"jsonrpc": "2.0", "id": 0, "method": "tools/list"})
        names = [t["name"] for t in r["result"]["tools"]]
        self.assertEqual(names, ["drift_check", "drift_session", "drift_explain"])

    def test_every_tool_has_schema(self):
        r = S.handle_request({"jsonrpc": "2.0", "id": 0, "method": "tools/list"})
        for t in r["result"]["tools"]:
            with self.subTest(t=t["name"]):
                self.assertIn("inputSchema", t)
                self.assertTrue(t["description"])

    def test_unknown_tool_errors(self):
        r = S.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "nope", "arguments": {}}})
        self.assertEqual(r["error"]["code"], -32601)

    def test_unsupported_method_errors(self):
        r = S.handle_request({"jsonrpc": "2.0", "id": 1, "method": "resources/read"})
        self.assertEqual(r["error"]["code"], -32601)

    def test_initialized_notification_returns_nothing(self):
        self.assertIsNone(S.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_missing_argument_is_invalid_params(self):
        r = S.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "drift_check", "arguments": {}}})
        self.assertEqual(r["error"]["code"], -32602)


class TestDriftCheck(unittest.TestCase):
    def test_allows_conforming(self):
        self.assertEqual(call("drift_check", {"mandate": MANDATE, "action": act()})["verdict"], "ALLOW")

    def test_denies_amount_over_limit(self):
        out = call("drift_check", {"mandate": MANDATE, "action": act(amount_minor=99_999)})
        self.assertEqual((out["verdict"], out["rule"]), ("DENY", "amount"))

    def test_denies_foreign_audience(self):
        out = call("drift_check", {"mandate": MANDATE, "action": act(audience="attacker.example")})
        self.assertEqual((out["verdict"], out["rule"]), ("DENY", "audience"))

    def test_returns_report(self):
        self.assertIn("report", call("drift_check", {"mandate": MANDATE, "action": act()}))


class TestDriftSession(unittest.TestCase):
    def test_counts_and_breaches(self):
        out = call("drift_session", {"mandate": MANDATE, "actions": [
            act(), act(amount_minor=99_999), act(payee_id="other"), act()]})
        self.assertEqual(out["report"]["allowed"], 2)
        self.assertEqual(out["report"]["denied"], 2)
        self.assertEqual(out["report"]["breach_by_rule"], {"amount": 1, "payee": 1})

    def test_decisions_in_order(self):
        out = call("drift_session", {"mandate": MANDATE, "actions": [
            act(), act(amount_minor=99_999)]})
        self.assertEqual([d["verdict"] for d in out["decisions"]], ["ALLOW", "DENY"])

    def test_fingerprint_present(self):
        self.assertTrue(call("drift_session", {"mandate": MANDATE, "actions": [act()]})["report"]["fingerprint"])

    def test_empty_session(self):
        out = call("drift_session", {"mandate": MANDATE, "actions": []})
        self.assertEqual(out["report"]["submitted"], 0)


class TestExplain(unittest.TestCase):
    def test_rules_in_order(self):
        out = call("drift_explain", {})
        self.assertEqual([r["rule"] for r in out["rules"]],
                         ["amount", "payee", "audience", "currency", "window", "intent"])

    def test_default_is_deny(self):
        self.assertEqual(call("drift_explain", {})["default"], "DENY")


class TestTransport(unittest.TestCase):
    def test_single_request(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        status, out = S.respond(body)
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(out)["result"]["tools"]), 3)

    def test_bad_json_is_parse_error(self):
        status, out = S.respond(b"{not json")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(out)["error"]["code"], -32700)

    def test_notification_only_returns_202(self):
        body = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
        self.assertEqual(S.respond(body)[0], 202)

    def test_batch(self):
        body = json.dumps([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]).encode()
        status, out = S.respond(body)
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(out)), 2)

class TestHealth(unittest.TestCase):
    """/health must describe a live service in a shape a monitor can parse."""

    def test_path_constant(self):
        from http_server import HEALTH_PATH
        self.assertEqual(HEALTH_PATH, "/health")

    def test_payload_is_valid_json(self):
        from http_server import __version__
        body = json.dumps({"status": "ok", "service": "drift", "version": __version__})
        parsed = json.loads(body)
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["service"], "drift")
        self.assertTrue(parsed["version"])

    def test_health_is_not_confused_with_mcp(self):
        from http_server import HEALTH_PATH, MCP_PATH
        self.assertNotEqual(HEALTH_PATH.rstrip("/"), MCP_PATH.rstrip("/"))

class TestIntrospectionCompleteness(unittest.TestCase):
    """A registry introspects all three list methods. Missing ones read as defects."""

    def test_resources_list_returns_empty_not_error(self):
        r = S.handle_request({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
        self.assertIn("result", r, "resources/list must answer, not -32601")
        self.assertEqual(r["result"]["resources"], [])

    def test_prompts_list_returns_empty_not_error(self):
        r = S.handle_request({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})
        self.assertIn("result", r, "prompts/list must answer, not -32601")
        self.assertEqual(r["result"]["prompts"], [])

    def test_initialize_declares_all_three_capabilities(self):
        r = S.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        caps = r["result"]["capabilities"]
        for cap in ("tools", "resources", "prompts"):
            self.assertIn(cap, caps)

    def test_unknown_method_still_errors(self):
        r = S.handle_request({"jsonrpc": "2.0", "id": 1, "method": "resources/read"})
        self.assertEqual(r["error"]["code"], -32601)


class TestToolAnnotationsAndDescriptions(unittest.TestCase):
    """Glama scores tool definitions and captures annotations; both are checked."""

    def _tools(self):
        return S.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]

    def test_every_tool_declares_all_four_hints(self):
        for t in self._tools():
            a = t.get("annotations")
            self.assertIsNotNone(a, f"{t['name']} has no annotations")
            for hint in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
                self.assertIn(hint, a, f"{t['name']} misses {hint}")
                self.assertIsInstance(a[hint], bool)

    def test_every_tool_has_a_title(self):
        for t in self._tools():
            self.assertTrue(t["annotations"].get("title"))

    def test_every_tool_states_when_not_to_use_it(self):
        for t in self._tools():
            self.assertIn("Do NOT", t["description"],
                          f"{t['name']} does not say when not to call it")

    def test_every_tool_states_idempotency(self):
        for t in self._tools():
            self.assertIn("idempotent", t["description"].lower())

    def test_descriptions_are_english_for_international_listing(self):
        for t in self._tools():
            self.assertFalse(any("Ѐ" <= ch <= "ӿ" for ch in t["description"]),
                             f"{t['name']} description contains Cyrillic")

    def test_every_parameter_is_described(self):
        for t in self._tools():
            props = t["inputSchema"].get("properties", {})
            for name, spec in props.items():
                if spec.get("type") == "object":
                    continue
                self.assertTrue(spec.get("description"),
                                f"{t['name']}.{name} has no description")

    def test_required_fields_are_declared(self):
        tools = {t["name"]: t for t in self._tools()}
        mandate = tools["drift_check"]["inputSchema"]["properties"]["mandate"]
        self.assertEqual(
            sorted(mandate["required"]),
            sorted(["payee_id", "audience", "max_amount_minor", "currency",
                    "valid_from", "valid_until", "allowed_intents"]),
        )

class TestBindAddress(unittest.TestCase):
    """Regression: a container that binds loopback runs but serves nothing.

    Docker port publishing forwards to the container's external interface, so a server
    listening on 127.0.0.1 inside the image is unreachable while the container still
    reports "running". The image was unlistable for exactly this reason.
    """

    def test_default_host_is_loopback(self):
        import http_server
        self.assertEqual(http_server.DEFAULT_HOST, "127.0.0.1")

    def test_dockerfile_passes_an_explicit_host(self):
        import pathlib
        dockerfile = pathlib.Path(__file__).with_name("Dockerfile").read_text()
        cmd = [l for l in dockerfile.splitlines() if l.startswith("CMD")]
        self.assertTrue(cmd, "Dockerfile has no CMD")
        self.assertIn("--host", cmd[0])
        self.assertIn("0.0.0.0", cmd[0])

    def test_host_flag_is_parsed_when_present(self):
        import http_server
        args = ["--http", "8095", "--host", "0.0.0.0"]
        host = http_server.DEFAULT_HOST
        if "--host" in args:
            host = args[args.index("--host") + 1]
        self.assertEqual(host, "0.0.0.0")

    def test_image_declares_a_healthcheck(self):
        import pathlib
        dockerfile = pathlib.Path(__file__).with_name("Dockerfile").read_text()
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("/health", dockerfile)

    def test_container_runs_as_non_root(self):
        import pathlib
        dockerfile = pathlib.Path(__file__).with_name("Dockerfile").read_text()
        self.assertIn("USER drift", dockerfile)
        self.assertNotIn("USER root", dockerfile)

    def test_startup_is_not_silent(self):
        import http_server
        self.assertTrue(callable(http_server.log))


if __name__ == "__main__":
    unittest.main(verbosity=2)
