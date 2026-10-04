---
title: "Six ways an agent exceeds its mandate — and the check that stops each"
description: "A mandate is checked once, at signature. Then the agent acts alone. Six concrete failure modes of mandate enforcement, in order of how often they matter, with the rule that catches each."
slug: six-ways-an-agent-drifts
tags: [ai-agents, authorization, security, mcp, observability]
---

# Six ways an agent exceeds its mandate

A mandate says: pay this payee, up to this amount, in this currency, until this date, for
this intent. The signature on that mandate is checked **once**.

After that, the agent is on its own. Six things can go wrong, and they are not equally
likely. Ranked by how often they actually matter:

## 1. Over the limit (`amount`)

The most common drift by a wide margin. Not malicious — arithmetic. A retry loop that
re-pays. A batch that was meant to be 3 items and became 30. A currency conversion applied
twice.

This is why `amount` is checked **first**. If the limit is blown, that is the answer the
operator needs. Reporting "currency mismatch" instead would be technically true and
practically useless.

## 2. Wrong recipient (`payee`)

The mandate names a payee. Something rewrites it: a config change, a compromised
dependency resolving a different name, a prompt injection that rewrites the destination.

## 3. Redirected audience (`audience`)

The subtle one. The mandate is bound to a merchant. The payment goes elsewhere.

This is not hypothetical — it is the failure mode that the AP2 Python SDK does not
currently catch, because its audience check is skipped for key-bound hops. See
[the write-up](https://mrpkk.github.io/drift/ap2-expected-aud-not-enforced/).

## 4. Currency substitution (`currency`)

Paying 10 000 in a currency the mandate did not authorise. Usually an integration that
defaults to a local currency when one is missing.

## 5. Outside the window (`window`)

A mandate with an expiry, honoured after it. Usually a retry of a queued payment, or a
cache that outlives the credential.

## 6. Intent outside the allowlist (`intent`)

The mandate allows `buy`. The agent does something else that happens to cost money.

`intent` arrives **from the agent**, so it is untrusted input. Checking it does not make
the intent true. It bounds what a lying agent can claim it is doing.

## Two design decisions that follow

**Check before, not after.** Every one of these six is cheap to prevent and expensive to
detect afterwards. The money is already gone by the time an after-the-fact check runs.

**Deny by default.** An action that matches no rule is not "probably fine". It is
unclassified. Unclassified means denied until someone classified it.

## What this does not solve

If the agent controls both `intent` and `amount`, it can lie in both, and no amount of
checking at the boundary helps. Bounding the damage and proving what happened are
different jobs:

- **drift** bounds — six rules, before the action, append-only ledger.
- **attest** proves — after the action, from an artifact.

An agent that lies within its limits still leaves a trail. That trail is what makes the
lie detectable.

## Try it

```
pip install drift
```

Three MCP tools: `drift_check` for one action, `drift_session` for a sequence with a
chain fingerprint, `drift_explain` to see the rules in the order they apply. Standard
library only, 42 tests.
