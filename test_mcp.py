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
        r = S.handle_request({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
