"""
Enforce an agent mandate before an action runs — as a Windmill step.

Windmill resource script. Standard library only, no dependencies.

Use this as the gate in front of anything an agent is about to do that
moves value: a payment, a transfer, a deploy. The check answers one
question — *was this permitted* — and it answers it at the moment the
action happens, not once when the mandate was signed.

Why that matters: protocols like AP2 verify a mandate once, at signing.
After that the agent acts on its own and nothing holds it in bounds. The
authority ends; the permission does not. This script is the continuation
of the check, run per action.

Example:
    verdict = main(
        mandate={"payee_id": "0xAAA", "audience": "client-7",
                 "max_amount_minor": 100000, "currency": "USDC",
                 "valid_from": 1000, "valid_until": 2000000,
                 "allowed_intents": ["transfer"]},
        action={"payee_id": "0xAAA", "audience": "client-7",
                "amount_minor": 50000, "currency": "USDC",
                "at": 1500, "intent": "transfer"},
    )
    if not verdict["allowed"]:
        raise RuntimeError(f"denied: {verdict['rule']}")

With ``enforce=True`` the script raises on a denial, so a Windmill flow
stops on its own instead of relying on the caller to check.

Rules checked, in this order: amount, payee, audience, currency, window,
intent. The order is fixed so the most common reason comes first — an
exceeded amount is the most likely form of drift.

Absence of a mandate is a denial, never consent: a verifier who presents
no permission has not demonstrated one.
"""

import urllib.request
import urllib.error
import json as _json
from typing import Any, Optional

DRIFT_URL = "https://drift.rta.lat/mcp"

_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    # Cloudflare answers 1010 without a User-Agent, which would make this
    # script fail for every user rather than only for us.
    "User-Agent": "windmill-drift-script/1.0 (+https://drift.rta.lat)",
}


def _call(tool: str, arguments: dict, url: str = DRIFT_URL, timeout: int = 30) -> Any:
    body = _json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": tool, "arguments": arguments}}
    ).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=_MCP_HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"drift unavailable: HTTP {exc.code} — "
            f"{exc.read().decode('utf-8', 'replace')[:160]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"drift unreachable: {exc.reason}") from exc

    # SSE answers arrive as data: lines; the JSON payload is on the last one.
    if raw.lstrip().startswith("data:"):
        payload = [ln[5:].strip() for ln in raw.splitlines() if ln.startswith("data:")]
        raw = payload[-1] if payload else "{}"
    envelope = _json.loads(raw)
    if "error" in envelope:
        raise RuntimeError(f"drift error: {envelope['error']}")
    result = envelope.get("result", {})
    if result.get("isError"):
        raise RuntimeError(result.get("content", [{}])[0].get("text", "drift error"))
    text = (result.get("content") or [{}])[0].get("text", "{}")
    try:
        return _json.loads(text)
    except _json.JSONDecodeError:
        return {"raw": text}


def _normalise(raw: Any) -> Optional[dict]:
    """drift answers with ``verdict: ALLOW|DENY``; the script speaks ``allowed``.

    Returning None when the shape is unrecognised is deliberate: a caller
    that cannot read the answer must not turn that into permission.
    """
    if not isinstance(raw, dict):
        return None
    if "allowed" in raw:                     # already normalised
        return raw
    verdict = raw.get("verdict")
    if verdict is None:
        return None
    return {
        "allowed": str(verdict).upper() == "ALLOW",
        "verdict": verdict,
        "rule": raw.get("rule", "-"),
        "note": raw.get("note", ""),
        "report": raw.get("report", {}),
    }


def main(
    mandate: dict,
    action: dict,
    enforce: bool = False,
    url: str = DRIFT_URL,
) -> dict:
    """
    Check one action against a mandate.

    :param mandate: payee_id, audience, max_amount_minor, currency,
                    valid_from, valid_until, allowed_intents (optional)
    :param action: payee_id, audience, amount_minor, currency, at, intent
    :param enforce: raise on a denial instead of returning it — lets a
                    Windmill flow stop on its own
    :param url: drift endpoint; override to point at your own deployment

    :return: {"allowed": bool, "rule": str, ...}
    :raises ValueError: if enforce is set and the action is denied
    """
    if not isinstance(mandate, dict) or not isinstance(action, dict):
        raise ValueError("mandate and action must both be objects")

    raw = _call("drift_check", {"mandate": mandate, "action": action}, url=url)
    verdict = _normalise(raw)

    # An empty or unrecognised answer must not read as permission. If drift
    # changes its shape we have no verdict — and "no verdict" is not "allowed".
    if verdict is None:
        verdict = {"allowed": False, "rule": "no_verdict",
                   "note": "drift returned no verdict; absence is not consent"}

    if enforce and not verdict.get("allowed"):
        raise ValueError(
            f"action denied by mandate: {verdict.get('rule', 'unknown')} "
            f"({verdict.get('note', '')})".strip()
        )
    return verdict


def check_many(
    mandate: dict,
    actions: list,
    stop_on_first_denial: bool = True,
    url: str = DRIFT_URL,
) -> dict:
    """
    Check a batch against one mandate.

    Batch checking is where drift pays off: a single mandate and five
    proposed actions are answered as one question, and the first denial
    usually identifies the whole problem.
    """
    if not isinstance(actions, list):
        raise ValueError("actions must be a list")
    if not actions:
        return {"allowed": True, "checked": 0, "denials": [], "results": []}

    raw = _call("drift_session", {"mandate": mandate, "actions": actions}, url=url)
    if not isinstance(raw, dict):
        return {"allowed": False, "checked": 0, "denials": [],
                "note": "no answer from drift; absence is not consent"}

    allowed_all = str(raw.get("verdict", "")).upper() == "ALLOW"
    per_action = [_normalise(r) for r in (raw.get("actions") or raw.get("results") or [])]
    per_action = [r for r in per_action if r]
    report = raw.get("report", {})
    denied_by = report.get("breach_by_rule") or {}
    total = report.get("submitted", len(per_action))
    # report.denied — число отклонений, а не список: перебор по нему
    # ронял скрипт на первом же пакетном вызове.
    denied = report.get("denied")
    if not isinstance(denied, int):
        denied = sum(1 for r in per_action if r and not r["allowed"])

    out = {
        "allowed": bool(allowed_all and total and denied == 0),
        "checked": total,
        "denied": denied,
        "breach_by_rule": denied_by,
        "results": per_action,
    }
    if not allowed_all and denied_by:
        out["first_denial_rule"] = next(iter(denied_by))
    return out


def explain(url: str = DRIFT_URL) -> dict:
    """What drift checks and what it does not."""
    return _call("drift_explain", {}, url=url)
