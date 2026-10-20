# The order of the checks is the product

A mandate is a signed permission: this payee, up to this amount, in this currency, until this
date, for this intent. The signature is verified once. Everything that matters happens after.

Most mandate checkers answer a different question than the one an operator asks. They validate
the fields and return **a list of everything that is wrong**: amount too high *and* payee not
allowed *and* intent not on the list. That looks thorough. In practice it is the wrong output,
because the operator has to decide what to do first, and a list does not answer that.

We check six rules in a **fixed order** and return **one verdict with one rule**:

```
amount → payee → audience → currency → window → intent
```

Whichever rule fails first is the answer. The rest of the failures are not lost — they are in
the ledger — but the refusal names the most important reason, not an incidental one.

## Why this order and not another

Each position is a decision about what an operator needs to hear first:

1. **amount** — the most common drift by far. Not malicious: retry loops, batches that grew,
   a conversion applied twice. If the limit is blown, that is the answer.
2. **payee** — money is about to leave for someone the mandate never named. More serious than
   the amount, but rarer.
3. **audience** — the merchant audience bound into the mandate. Catches the case where the
   payee is right but the payment is leaving for the wrong context.
4. **currency** — a substitution (USDC for USD, or a token with the same ticker).
5. **window** — the mandate expired (or has not started). Cheap to check, rarely the first
   thing an operator cares about.
6. **intent** — "the action is something the mandate never allowed". This is last on purpose:
   intent arrives from the agent and is untrusted input. It is checked against an allowlist,
   never believed — but it should not outrank money facts.

If an action breaks both `amount` and `intent`, the operator gets `DENY amount`. That is the
rule that explains the damage.

## Deny by default

An unclassified action is refused. `allowed_intents` empty means "no intent is allowed", not
"anything goes". Silence is not permission — the most common security bug in authorization
code is the branch nobody wrote.

## A denial is evidence, not a log line

Every decision goes into an append-only ledger, and the ledger carries a chain fingerprint.
Kill the process, edit the file, reorder the entries — the fingerprint stops matching.

A real session against the live checker, three actions, one mandate (cap $500, payee
`stripe-refunds`, intents `refund` / `chargeback`):

```json
{"decisions": [
   {"verdict": "ALLOW", "rule": "-",      "at": 1792395952},
   {"verdict": "DENY",  "rule": "amount", "at": 1792395982},
   {"verdict": "DENY",  "rule": "payee",  "at": 1792396012}],
 "report": {
   "submitted": 3, "allowed": 1, "denied": 2,
   "breach_by_rule": {"amount": 1, "payee": 1},
   "fingerprint": "596079e81359655b"}}
```

Three refunds were proposed: $240 (inside the mandate), $840 (over the cap), $240 to a
different payee. One went through, two were stopped, and the batch ends with **one
fingerprint**. That is the artifact you hand to an auditor: not "we logged it", but "here is
what was decided, in order, and it cannot be edited without breaking the chain".

The order in the output is the order of submission. The order of the *rules* is what made the
second row say `amount` instead of something else.

## The API is one call

The checker is an MCP tool (`drift_check` for one action, `drift_session` for a batch), and a
plain HTTPS endpoint. The same body as above:

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
 "params": {"name": "drift_session",
            "arguments": {"mandate": {...}, "actions": [...]}}}
```

Endpoint: `https://drift.rta.lat/mcp` — 100 checks/day free, no account, no key. Self-hosted
is the same code, Apache-2.0, standard library only.

## Honest limits

- The checker **does not verify mandate signatures** — that is AP2's job, and it runs before us.
- It **does not execute the action** and does not prove what actually happened afterwards.
  It decides, before the fact; an audit tool proves what happened after.
- `intent` is untrusted input by design. An agent that lies within its limits still leaves a
  ledger entry — the ledger makes the lie visible, it does not prevent it.

If you want the enforcement loop packaged for an agent (the loop, the six rules with edge
cases, mandate templates for trading/shopping/refunds, connection guide), it is on Agensi as a
skill: https://www.agensi.io/skills/drift-mandate-guard-for-ai-agents

The rules themselves are in the repository — read them and try to break them:
https://github.com/mrpkk/drift/blob/main/drift.py
