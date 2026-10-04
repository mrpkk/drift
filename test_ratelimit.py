"""Tests for the free-tier rate limiter. Deterministic: the clock is injected."""

from __future__ import annotations

import json
import pathlib

import unittest

from ratelimit import DAY_SECONDS, Decision, RateLimiter, client_key


class FakeClock:
    """Manually advanced clock. Never sleeps, so the suite cannot flake."""

    def __init__(self, start: int = 1_700_000_000) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += seconds


class TestFixedWindow(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.rl = RateLimiter(limit=3, window_seconds=DAY_SECONDS, clock=self.clock)

    def test_allows_exactly_the_limit(self):
        for i in range(3):
            d = self.rl.check("a")
            self.assertTrue(d.allowed, f"call {i + 1} should be allowed")
        self.assertEqual(self.rl.peek("a").remaining, 0)

    def test_denies_the_call_after_the_limit(self):
        for _ in range(3):
            self.rl.check("a")
        d = self.rl.check("a")
        self.assertFalse(d.allowed)
        self.assertEqual(d.remaining, 0)

    def test_remaining_counts_down(self):
        seen = [self.rl.check("a").remaining for _ in range(3)]
        self.assertEqual(seen, [2, 1, 0])

    def test_window_rolls_over(self):
        for _ in range(3):
            self.rl.check("a")
        self.assertFalse(self.rl.check("a").allowed)
        self.clock.advance(DAY_SECONDS)
        self.assertTrue(self.rl.check("a").allowed)

    def test_retry_after_counts_down_to_the_boundary(self):
        for _ in range(3):
            self.rl.check("a")
        first = self.rl.check("a").retry_after
        self.clock.advance(DAY_SECONDS - 10)
        self.assertEqual(self.rl.check("a").retry_after, 10)

    def test_reset_at_is_the_window_boundary(self):
        d = self.rl.check("a")
        self.assertEqual(d.reset_at, int(self.clock.now) + DAY_SECONDS)

    def test_keys_are_independent(self):
        for _ in range(3):
            self.rl.check("a")
        self.assertFalse(self.rl.check("a").allowed)
        self.assertTrue(self.rl.check("b").allowed)

    def test_peek_does_not_consume(self):
        for _ in range(5):
            self.rl.peek("a")
        self.assertEqual(self.rl.peek("a").remaining, 3)
        self.assertTrue(self.rl.check("a").allowed)

    def test_zero_limit_denies_everything(self):
        rl = RateLimiter(limit=0, window_seconds=DAY_SECONDS, clock=self.clock)
        d = rl.check("a")
        self.assertFalse(d.allowed)
        self.assertEqual(d.limit, 0)

    def test_reset_clears_all_keys(self):
        for _ in range(3):
            self.rl.check("a")
        self.rl.reset()
        self.assertEqual(self.rl.active_keys(), 0)
        self.assertTrue(self.rl.check("a").allowed)

    def test_active_keys_counts_distinct_callers(self):
        self.rl.check("a")
        self.rl.check("b")
        self.rl.check("b")
        self.assertEqual(self.rl.active_keys(), 2)

    def test_headers_expose_quota(self):
        d = self.rl.check("a")
        h = d.headers()
        self.assertEqual(h["X-RateLimit-Limit"], "3")
        self.assertEqual(h["X-RateLimit-Remaining"], "2")
        self.assertEqual(h["Retry-After"], "0")


class TestValidation(unittest.TestCase):
    def test_negative_limit_rejected(self):
        with self.assertRaises(ValueError):
            RateLimiter(limit=-1)

    def test_non_positive_window_rejected(self):
        with self.assertRaises(ValueError):
            RateLimiter(limit=1, window_seconds=0)


class TestDocumentedBoundaryBurst(unittest.TestCase):
    """The fixed-window trade-off is accepted, so it is pinned by a test.

    A caller may send up to 2x the limit across a window boundary. That ceiling is the
    known cost of a fixed window and must not regress silently.
    """

    def test_boundary_allows_a_burst_of_at_most_double(self):
        clock = FakeClock()
        rl = RateLimiter(limit=5, window_seconds=DAY_SECONDS, clock=clock)
        for _ in range(5):
            self.assertTrue(rl.check("a").allowed)
        self.assertFalse(rl.check("a").allowed)
        clock.advance(DAY_SECONDS)
        granted = sum(1 for _ in range(10) if rl.check("a").allowed)
        self.assertEqual(granted, 5, "a fresh window grants the limit again, not more")


class TestClientKey(unittest.TestCase):
    def test_forwarded_header_wins_over_peer(self):
        k = client_key({"X-Forwarded-For": "203.0.113.7, 70.41.3.18"}, ("127.0.0.1", 5555))
        self.assertEqual(k, "ip:203.0.113.7")

    def test_first_hop_only(self):
        k = client_key({"x-forwarded-for": "198.51.100.9 , 10.0.0.1"}, None)
        self.assertEqual(k, "ip:198.51.100.9")

    def test_falls_back_to_peer(self):
        self.assertEqual(client_key({}, ("192.0.2.4", 1234)), "ip:192.0.2.4")

    def test_anonymous_when_nothing_known(self):
        self.assertEqual(client_key(None, None), "anonymous")

    def test_empty_forwarded_falls_through_to_peer(self):
        k = client_key({"X-Forwarded-For": "  "}, ("192.0.2.4", 1))
        self.assertEqual(k, "ip:192.0.2.4")


class TestDecisionShape(unittest.TestCase):
    def test_decision_is_frozen(self):
        d = Decision(True, 1, 0, 0, 0)
        with self.assertRaises(Exception):
            d.allowed = False  # type: ignore[misc]

class TestFreeTierWiring(unittest.TestCase):
    """The quota must be charged per request, before parsing, and never on /health."""

    def test_default_free_tier_is_one_hundred_per_day(self):
        import http_server
        self.assertEqual(http_server.FREE_TIER_PER_DAY, 100)

    def test_rate_limit_can_be_disabled_by_env(self):
        import http_server
        self.assertEqual(http_server.DISABLE_RATE_LIMIT_ENV, "DRIFT_NO_RATE_LIMIT")
        self.assertIsNone(http_server.LIMITER)  # создаётся только в main()

    def test_env_var_name_matches_what_main_reads(self):
        """Имя переменной в константе и в main() должно быть одной строкой."""
        import http_server
        src = pathlib.Path(http_server.__file__).read_text()
        self.assertIn("os.environ.get(DISABLE_RATE_LIMIT_ENV)", src)

    def test_health_is_not_charged(self):
        """A monitor polling /health must not eat a caller's daily quota."""
        import http_server
        body = json.dumps({"status": "ok", "service": "drift", "version": "0.1.0"})
        self.assertIn("status", body)
        #/health обрабатывается до блока квоты в do_POST и до неё же в do_GET
        src = pathlib.Path(http_server.__file__).read_text()
        post = src.split("def do_POST")[1].split("def do_GET")[0]
        self.assertLess(post.index("HEALTH_PATH"), post.index("LIMITER.check"))

    def test_quota_is_checked_before_parsing_the_body(self):
        """An exhausted caller must not make the server read or parse anything."""
        import http_server
        src = pathlib.Path(http_server.__file__).read_text()
        post = src.split("def do_POST")[1].split("def do_GET")[0]
        self.assertLess(post.index("LIMITER.check"), post.index("self.rfile.read"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
