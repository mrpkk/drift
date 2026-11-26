# drift

[![M8ven Score](https://m8ven.ai/badge/mcp/mrpkk-drift-52f8ee?v=1efa9c1d58839ca73fb2be488b6f4816)](https://m8ven.ai/mcp/mrpkk-drift-52f8ee?s=readme)
[![Listed on mcpservers.org](https://mcpservers.org/badge.svg)](https://mcpservers.org/servers/mrpkk/drift)
[![mrpkk/drift MCP server](https://glama.ai/mcp/servers/mrpkk/drift/badges/score.svg)](https://glama.ai/mcp/servers/mrpkk/drift)

**Continuous mandate enforcement for AI agents.** Every action is checked against the
signed mandate *before* it happens, not once at signing.

Live: **<https://drift.rta.lat>** · MCP endpoint: **<https://drift.rta.lat/mcp>**

```
pip install drift          # or: pip install -e .
python3 -m drift.http_server --http 8080
```


## Permissions and the state volume

The container starts as root for exactly one step: it creates the state directory and hands it to
user `10001`, then drops privileges with `setpriv`. The application itself never runs as root
(`/proc/1/status` → `Uid: 10001`). Without that step a named volume is mounted `root:root` and the
first paid call fails with `Permission denied` when writing the payment journal — verified and fixed
on 26.11.2026. Check the result yourself:

```
docker compose exec drift cat /proc/1/status | grep Uid      # 10001
docker compose exec drift ls -ld /data/a                     # owned by 10001
```

## Self-host in four commands

```bash
git clone https://github.com/mrpkk/drift && cd drift
docker compose up -d --build
curl -s http://127.0.0.1:8095/health          # {"status": "ok", ...}
docker compose ps                            # ждём Up (healthy), не просто Up
curl -s -X POST http://127.0.0.1:8095/mcp -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Port already taken? `DRIFT_PORT=18095 docker compose up -d` — наружу по-прежнему только `127.0.0.1`.

Проверено 22.11.2026 на чистой машине: `docker compose build` → `up -d` → контейнер
`Up (healthy)` (23.11.2026: проверено живьём — `Up 23 seconds (healthy)`, `/health` 200 за 4 мс),
`/health` → 200, `tools/list` → список инструментов. Образ запускается от
пользователя `drift` (uid 10001), корневая ФС read-only, `no-new-privileges`.

What you get by default:

| | |
|---|---|
| Transport | HTTP on `127.0.0.1:8095` (не в интернете — публикация это ваше решение) |
| Quota | 100 free checks per day (`DRIFT_FREE_TIER_PER_DAY`) |
| Payment | off until `DRIFT_X402_ENABLED=1` and `DRIFT_X402_PAY_TO` are set |
| State | volume `drift-state` → `/data`; persists `/data/a/payments.json` (payment journal). Free-tier counters and sessions are in memory and reset on restart |
| Hardening | `read_only: true`, `no-new-privileges`, non-root user in the image |
| Healthcheck | `GET /health` каждые 30 с (в образе и в compose) |

Turn the paid rail on (optional):

```bash
DRIFT_X402_ENABLED=1 DRIFT_X402_PAY_TO=0xYourAddress docker compose up -d
# default is Base Sepolia: the bundled facilitator (x402.org) does not serve mainnet.
# For real money set both:
#   DRIFT_X402_NETWORK=eip155:8453 DRIFT_X402_FACILITATOR=<mainnet facilitator> DRIFT_X402_ENABLED=1
```

Stdio mode stays available for local MCP clients:

```bash
docker run --rm -i drift-mandate:local python3 mcp_server.py
```

Nothing else is required: no database, no external service, no keys to fetch at build
time. If the container is unhealthy, `docker compose logs drift` shows the reason.

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

## Buying the setup ($79)

The code here is the engine. The **setup** — a hard per-task budget, a payee
allowlist and a kill switch wired into your stack, tested on a dry run, with a
runbook and a signed refusal receipt — is a paid job: **$79, 48 hours**.

- Card: <https://nidhipati.ailegal.legal/pay/card?product=drift-kill-switch>
- USDC on **Base**: address is on the offer page (<https://rta.lat/drift-offer/>) and in
  `offer/index.html`; it is published from `~/.env` by `scripts/publish_offer.py`, so the
  repository and the page cannot disagree for long.
- After a transfer, send the transaction hash to maxim.pkk@gmail.com. The order starts when the
  transfer is seen on-chain, not when the email arrives.

### Checking a USDC payment

`scripts/check_usdc_payment.py` reads the Base transaction receipt through a public RPC and looks
for the USDC `Transfer` event into our address — the amount from a form or an email is not evidence.

```bash
python3 scripts/check_usdc_payment.py --tx 0x… --min 79
```

What it proves: the transfer exists, went to this address, in this token, for at least this amount.
What it does not prove: that the sender was the buyer, that the transfer cannot be reversed, or that
a second transfer will not arrive later. Two transfers add up; call it once per hash.

## Use it from your agent

```json
{
  "mcpServers": {
    "drift": { "url": "https://drift.rta.lat/mcp" }
  }
}
```

That block is how our own agents call this server. Tools: `drift_check` (one
action), `drift_session` (a sequence), `drift_explain` (the six rules and why
each exists). 100 free checks per day, then $0.005 per call over x402.

## Licence

Apache-2.0.
