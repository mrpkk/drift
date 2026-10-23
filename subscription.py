"""30 days of unlimited mandate checks, prepaid in one payment.

Why a token and not an account: drift has no signup, no database and no
session. The only thing the server can recognise is a value it signed itself.
The token is therefore self-contained — plan, issue time, expiry, optional
subject — and verified with HMAC-SHA256 over the exact bytes, so editing any
field invalidates it.

Why HMAC and not Ed25519: this repository has no dependencies (see
pyproject.toml), and the token is verified by the same process that signs it.
A public-key signature would only be needed if a third party had to check a
token without holding the secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from decimal import Decimal

PLAN = "drift-unlimited-30d"
PRICE_USDC = Decimal("9")
DAYS = 30
HEADER = "X-DRIFT-ENTITLEMENT"
SECRET_ENV = "DRIFT_SUB_SECRET"


def _secret() -> str:
    return os.environ.get(SECRET_ENV, "").strip()


def enabled() -> bool:
    """Without a secret the plan cannot be sold: a token nobody can verify is worse."""
    return bool(_secret())


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def issue(now: float | None = None, subject: str = "") -> str:
    """Mint a token. Raises RuntimeError when the deployment has no secret."""
    secret = _secret()
    if not secret:
        raise RuntimeError(f"{SECRET_ENV} is not set: nothing to sign with")
    issued = int(now if now is not None else time.time())
    payload = {
        "plan": PLAN,
        "issued_at": issued,
        "expires_at": issued + DAYS * 86400,
        "subject": subject[:64],
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).rstrip(b"=")
    return f"{body.decode('ascii')}.{_sign(secret, body)}"


def verify(token: str | None, now: float | None = None) -> dict | None:
    """Return the payload of a genuine, unexpired token; None for anything else.

    Every failure returns None on purpose: the caller must not learn whether a
    token was forged, expired or merely truncated.
    """
    secret = _secret()
    if not secret or not token:
        return None
    body_b64, _, signature = token.partition(".")
    if not body_b64 or not signature:
        return None
    try:
        body = body_b64.encode("ascii")
    except UnicodeEncodeError:
        return None
    expected = _sign(secret, body)
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        padded = body + b"=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("plan") != PLAN:
        return None
    try:
        expires_at = int(payload.get("expires_at") or 0)
    except (TypeError, ValueError):
        return None
    if expires_at <= int(now if now is not None else time.time()):
        return None
    return payload


def describe() -> dict:
    """What the plan is, for the 402 body and the pricing page."""
    return {
        "plan": PLAN,
        "price": str(PRICE_USDC),
        "currency": "USDC",
        "period_days": DAYS,
        "header": HEADER,
        "billing": "one-time prepayment",
        "auto_renews": False,
        "limit": "no daily cap while the token is valid",
    }
