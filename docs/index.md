---
layout: default
title: drift
---

# drift

**Continuous mandate enforcement for AI agents.** Every action is checked against the
signed mandate *before* it happens, not once at signing.

```bash
pip install drift
```

| tool | purpose |
|---|---|
| `drift_check` | check one action against a mandate |
| `drift_session` | check a sequence, get a chain fingerprint |
| `drift_explain` | show the rules in the order they apply |

Six rules, deny by default: `amount` · `payee` · `audience` · `currency` · `window` · `intent`.
Standard library only. [Source](https://github.com/mrpkk/drift) ·
[MCP manifest](https://github.com/mrpkk/drift/blob/master/server.json)

## Why

An agent is granted a mandate — who to pay, how much, in which currency, until when.
AP2 verifies that mandate **once, at signature time**. After that the agent acts alone
and nothing holds it inside its authority.

The gap is measurable. In the AP2 Python SDK, `verify_chain` documents that it enforces
`expected_aud` on the terminal hop, but the audience check is skipped for exactly the
key-bound hops the SDK itself produces:

```
aud in token  : https://attacker.example/ap2
expected_aud  : https://honest-merchant.example/ap2
ACCEPTED a token with the wrong audience.
```

[Read the write-up →]({{ '/ap2-expected-aud-not-enforced/' | relative_url }})
