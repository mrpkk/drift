# Lobsters — подача

**Title:** Continuous mandate enforcement for AI agents: AP2 skips the audience check

**Tags:** `security`, `programming`, `ai`, `opensource`, `paid`

**Short blurb** (для ленты):

Google's AP2 SDK documents that verify_chain enforces expected_aud on the terminal hop.
It does not, for the very token format the SDK itself produces. Reproducer and a six-rule
checker that closes the gap, with 145 tests and no dependencies.

**Body** — файл `hackernews.md`, сокращённый до ~700 символов:

In the AP2 Python SDK, verify_expected_claims runs only when typ in TYP_TERMINAL.
A kb+sd-jwt+kb token — which the SDK produces whenever the payload carries a cnf —
falls into TYP_INTERMEDIATE, so expected_aud is never checked. A token minted for
attacker.example is accepted when the verifier expects honest-merchant.example.

drift closes that: six rules, checked before the action, deny by default, integer money,
append-only fingerprinted ledger. MCP server, stdlib only, 145 tests.

https://github.com/mrpkk/drift
