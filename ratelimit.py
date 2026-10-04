"""Rate limiting for the free tier: N checks per day, no key, no registration.

Design constraints, in priority order:

1. **No registration.** An anonymous caller must get a useful answer without an account,
   an email, or an API key. The only thing required is that the limit exists at all.
2. **The core stays pure.** ``drift.py`` knows nothing about HTTP, clocks or quotas. A
   mandate check is a pure function of mandate and action; putting a counter in it would
   make the security-critical path depend on wall-clock time.
3. **Deterministic tests.** The clock is injected, so limit behaviour is tested without
   sleeping and without flaking on a slow machine.

Known limitation, stated rather than hidden: this is a **fixed window**, so a caller can
send ``2 * limit`` checks across a window boundary. That is accepted deliberately. A
sliding window would need per-request timestamps kept for every key, which turns an
in-memory counter into a growing store, and an in-memory store is lost on restart anyway.
The abuse ceiling that actually matters here is bounded by the free-tier price of one
check, which is zero, so the exposure is CPU, not money.

Thread safety: the handler is guarded by a lock. ``http.server`` is single-threaded by
default, but the limiter must stay correct if that ever changes.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Mapping

__all__ = ["Decision", "RateLimiter", "client_key"]

DAY_SECONDS = 86_400


@dataclass(frozen=True)
class Decision:
    """Outcome of one quota check. Returned even when allowed, so callers can log it."""

    allowed: bool
    limit: int
    remaining: int
    reset_at: int
    retry_after: int

    def headers(self) -> dict[str, str]:
        """Standard rate-limit headers, so any client or proxy can read the state."""
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset_at),
            "Retry-After": str(self.retry_after),
        }


class RateLimiter:
    """Fixed-window counter keyed by an arbitrary string.

    ``clock`` returns epoch seconds and is injectable so tests control time exactly.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: int = DAY_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if limit < 0:
            raise ValueError("limit must not be negative")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._used: dict[str, tuple[int, int]] = {}

    @property
    def limit(self) -> int:
        return self._limit

    def check(self, key: str) -> Decision:
        """Record one use against ``key`` and report whether it is within quota."""
        now = int(self._clock())
        with self._lock:
            window_start, count = self._used.get(key, (0, 0))
            if now - window_start >= self._window:
                window_start, count = now, 0
            reset_at = window_start + self._window
            if count < self._limit:
                count += 1
                self._used[key] = (window_start, count)
                return Decision(True, self._limit, self._limit - count, reset_at, 0)
            self._used[key] = (window_start, count)
            return Decision(
                False, self._limit, 0, reset_at, max(1, reset_at - now)
            )

    def peek(self, key: str) -> Decision:
        """Report current quota for ``key`` without consuming any."""
        now = int(self._clock())
        with self._lock:
            window_start, count = self._used.get(key, (0, 0))
            if now - window_start >= self._window:
                window_start, count = now, 0
            reset_at = window_start + self._window
            return Decision(
                count < self._limit,
                self._limit,
                max(0, self._limit - count),
                reset_at,
                max(1, reset_at - now),
            )

    def reset(self) -> None:
        with self._lock:
            self._used.clear()

    def active_keys(self) -> int:
        """How many distinct keys are tracked. Used by tests and diagnostics."""
        with self._lock:
            return len(self._used)


def client_key(
    headers: Mapping[str, str] | None = None,
    peer: tuple[str, int] | None = None,
) -> str:
    """Identify an anonymous caller.

    ``X-Forwarded-For`` wins over the socket peer because the service is expected to sit
    behind a tunnel or reverse proxy, where every request otherwise shares one peer
    address and a single caller would exhaust the quota of all the others.

    Only the first hop of the chain is trusted. A client can spoof this header when it
    reaches the service directly, so it raises the ceiling a single caller can consume;
    it does not grant anything beyond the free tier, which is free anyway.
    """
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
    forwarded = hdrs.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return f"ip:{first}"
    if peer:
        return f"ip:{peer[0]}"
    return "anonymous"
