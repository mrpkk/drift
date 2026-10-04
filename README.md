# drift

**Continuous mandate enforcement for AI agents.** Every action is checked against the
signed mandate *before* it happens, not once at signing.

```
pip install drift          # or: pip install -e .
python3 -m drift.http_server --http 8080
```

## The gap

An agent is granted a mandate: who to pay, how much, in which currency, until when.
AP2 verifies that mandate **once, at signature time**. After that the agent acts alone,
and nothing holds it inside its authority.

Measured proof that this matters, from the AP2 Python SDK itself:

```
verify_chain documents:
  "enforcing expected_aud / expected_nonce on the terminal hop when provided"

but kb_sd_jwt.verify calls verify_expected_claims only when typ in TYP_TERMINAL,
and a kb+sd-jwt+kb token — which the SDK itself produces whenever the payload
carries a cnf — falls into TYP_INTERMEDIATE:

  hop type      : kb+sd-jwt+kb
  aud in token  : https://attacker.example/ap2
  expected_aud  : https://honest-merchant.example/ap2
  ACCEPTED a token with the wrong audience.
```

The protocol an agent trusts with its spending authority does not check that the
authority was not substituted. drift checks it.

## What it checks

| # | rule | catches |
|---|---|---|
| 1 | `amount` | over the limit — the most common form of drift |
| 2 | `payee` | wrong recipient |
| 3 | `audience` | payment redirected to someone else |
| 4 | `currency` | currency substituted |
| 5 | `window` | outside the validity period |
| 6 | `intent` | intent outside the allowlist |

Default is DENY. Rules run in fixed order, so a refusal names the most important reason
rather than an incidental one.

## MCP tools

| tool | purpose |
|---|---|
| `drift_check` | check one action against a mandate |
| `drift_session` | check a sequence, get a report with a chain fingerprint |
| `drift_explain` | show the rules in the order they apply |

No SSE, no stateful sessions, no HTTP framework. Standard library only.

## Example

```python
from drift import Mandate, Drift, Action

m = Mandate(
    payee_id="shop", audience="https://merchant.example/ap2",
    max_amount_minor=10_000, currency="USD",
    valid_from=1_700_000_000, valid_until=1_700_086_400,
    allowed_intents=("buy",),
)
d = Drift(m)
verdict, rule = d.submit(Action(
    intent="buy", payee_id="shop", audience="https://merchant.example/ap2",
    amount_minor=99_999, currency="USD", at=1_700_000_060,
))
print(verdict, rule)   # DENY amount
```

## Limits, stated plainly

- **Does not verify mandate signatures.** That is AP2's job. drift runs *after* signature verification, at the level of decisions.
- **Does not execute.** It decides; the caller acts.
- **Does not keep state.** A session is an argument. No state means no attack surface and no divergence under replication.
- **Does not tell truth from intent.** `intent` arrives from the agent and is untrusted input. drift bounds the damage; it does not certify the intent.

If an agent controls both `intent` and `amount`, it can lie in both. Closing that gap is
`attest`'s job: attest proves afterwards, drift bounds beforehand. They are complements.

## Licence

Apache-2.0.
