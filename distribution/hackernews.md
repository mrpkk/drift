# Hacker News — Show HN

**Заголовок** (до 80 символов):

    Show HN: drift – continuous mandate enforcement for AI agents

**Текст** ( HN не поддерживает Markdown, только plain text и ссылки):

An agent is granted a mandate: who to pay, how much, in which currency, until when.
Google's AP2 protocol validates that mandate — but it validates it ONCE, at signature
time. After that the agent acts alone, and nothing holds it inside its authority.

The gap is not theoretical. In Google's own AP2 Python SDK, verify_chain documents that
it enforces expected_aud on the terminal hop. But kb_sd_jwt.verify calls
verify_expected_claims only when typ in TYP_TERMINAL — and a kb+sd-jwt+kb token, which
the SDK itself produces whenever the payload carries a cnf, falls into TYP_INTERMEDIATE:

    aud in token  : https://attacker.example/ap2
    expected_aud  : https://honest-merchant.example/ap2
    -> ACCEPTED a token with the wrong audience

So the protocol an agent trusts with its spending authority does not verify that the
authority was not substituted. drift checks it.

Six rules, evaluated before the action, deny by default:
amount (checked first — over-limit is the most common drift, and naming any other reason
would be technically true and practically useless), payee, audience, currency, window,
intent. Integer minor units for money. Frozen mandate. Append-only ledger with a chain
fingerprint, so rewriting history changes the hash and becomes visible.

MCP server: drift_check, drift_session, drift_explain. stdio and streamable-http.
Standard library only, no dependencies. 145 tests.

Free: 100 checks a day, no registration, no key.

Repo:    https://github.com/mrpkk/drift
Write-up: https://mrpkk.github.io/drift/ap2-expected-aud-not-enforced/
Second:   https://mrpkk.github.io/drift/six-ways-an-agent-drifts/

What drift does not do: verify mandate signatures (AP2's job), execute the action,
keep state between calls, or certify that an intent is truthful — intent arrives from
the agent and is untrusted input. An agent that lies within its limits still leaves a
ledger entry; proving what really happened is a different problem.

Feedback welcome, especially on whether the rule order is right.
