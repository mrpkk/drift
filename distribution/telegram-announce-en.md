🛡️ **drift** — continuous mandate enforcement for AI agents

An agent is granted a mandate: who to pay, how much, in which currency, until when.
AP2 verifies that mandate **once, at signature time**. After that the agent acts alone.

This is not theoretical. In Google's AP2 Python SDK, `verify_chain` documents that it
enforces `expected_aud` on the terminal hop. But `verify_expected_claims` runs only when
`typ in TYP_TERMINAL`, and a `kb+sd-jwt+kb` token — which the SDK itself produces whenever
the payload carries a `cnf` — falls into `TYP_INTERMEDIATE`:

  aud in token  : https://attacker.example/ap2
  expected_aud  : https://honest-merchant.example/ap2
  → ACCEPTED a token for the wrong audience

The protocol an agent trusts with its spending authority does not verify that the
authority was not substituted. drift does.

Six rules, checked before the action, deny by default:
`amount` · `payee` · `audience` · `currency` · `window` · `intent`
Integer minor units for money. Frozen mandate. Append-only ledger with a chain fingerprint.

MCP: `drift_check` · `drift_session` · `drift_explain`. Standard library only.
Free: 100 checks a day, no registration. 136 tests green (21 core, 41 protocol, 26 rate limit, 48 x402).

Code and write-up: https://github.com/mrpkk/drift
The defect analysis: https://mrpkk.github.io/drift/ap2-expected-aud-not-enforced/

If your agent spends money, it needs this barrier.
