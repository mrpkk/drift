---
title: "Your agent's spending mandate is not being audience-checked"
description: "In Google's AP2 Python SDK, verify_chain documents that it enforces expected_aud on the terminal hop. The check never runs for the tokens the SDK itself produces. Reproducer inside."
slug: ap2-expected-aud-not-enforced
tags: [ai-agents, security, ap2, authorization, mcp]
---

# Your agent's spending mandate is not being audience-checked

An agent that can spend money holds a mandate: who to pay, how much, in which currency,
until when. Google handed that mandate a real specification — **AP2**, Agent Payments
Protocol — which went to the FIDO Alliance on 28 April 2026.

The interesting question is not whether the specification is good. It is whether the
reference implementation enforces it.

## The claim in the code

`verify_chain` in the AP2 Python SDK documents:

> enforcing `expected_aud` / `expected_nonce` on the terminal hop when provided

So the chain verifier says it checks the audience on the last hop. That is exactly the
hop where a redirect would happen.

## What the code does

`kb_sd_jwt.verify` calls `verify_expected_claims` only under this condition:

```python
if typ in TYP_TERMINAL:
    verify_expected_claims(...)
```

And here is the problem. A `kb+sd-jwt+kb` token — the *key binding* format, which the
SDK itself produces **whenever the payload carries a `cnf` claim** — is classified as
`TYP_INTERMEDIATE`, not `TYP_TERMINAL`.

So for any mandate that binds a key, the audience check is skipped.

## Reproducer

```
hop type      : kb+sd-jwt+kb
aud in token  : https://attacker.example/ap2
expected_aud  : https://honest-merchant.example/ap2

result        : ACCEPTED
```

A token minted for `attacker.example` is accepted by a verifier that was told to expect
`honest-merchant.example`. No error. No warning. The verification returns success.

## Why this matters more than a normal bug

Most protocol bugs are wrong arithmetic. This one is a **missing branch on the security
path**, in the reference implementation, in the language with the only working verifier.

A verifier that accepts the wrong audience cannot detect a redirected mandate. The
attacker does not need to forge a signature. They need the merchant's public key, which
is public by design, and a mandate that was never bound to them.

## The scope, stated honestly

- This is **one hop type** in **one language**. Go and PHP implementations exist and
  neither can verify a mandate at all, so there is nothing to compare against.
- It may be a semantics dispute rather than a bug — a maintainer may argue that
  `expected_aud` is only meaningful on terminal hops. If so, the documentation is what
  is wrong, and it is wrong in the direction that makes implementers trust a check that
  does not happen.
- It is not a claim that AP2 is unsafe in general. It is a claim that one documented
  guarantee is not delivered on one path.

## What to do about it

1. **Implementers:** do not treat `verify_chain` as an audience check. Check `aud`
   yourself on every hop, in every language.
2. **Protocol authors:** the gap is in classification, not in cryptography. Either
   `kb+sd-jwt+kb` counts as terminal, or `expected_aud` is enforced unconditionally.
3. **Agent builders:** if your agent holds a spending mandate, verify the mandate
   *continuously*, not once at signature. That is what [`drift`](https://github.com/mrpkk/drift)
   does — six rules, deny by default, checked before each action.
4. **FIDO members:** this is an implementation-review item for the working group.

## Verify it yourself

The finding came from reading one file and writing one script, not from a research budget:

```
pip install drift
python3 test_drift.py
```

The negative vectors in the contribution reproduce the acceptance. The Go verifier exists
so the claim does not rest on a single implementation.
