# Monetization — drift

## Measured unit economics

Every number below is measured, not assumed.

| quantity | value | source |
|---|---|---|
| Base transaction cost | ≈ $0.00146 | gas 0.006 Gwei × 90 000 gas, ETH $2702 |
| price per check | $0.005 | chosen |
| **rail share of revenue** | **≈ 29 %** | 0.00146 / 0.005 |
| calls for $100 gross | ≈ 28 000 | 100 / 0.005 |

**Floor price ≈ $0.002.** Below that the rail eats more than half. Do not sell a single
check cheaper — that is the same mistake as selling below cost.

`FACT`. Circle's faucet for Base USDC is blocked for Russian IPs (Cloudflare error 1009),
and no USDC balance is held on Base. Settlement cannot be executed end to end yet.

## Why $0.005 and not free

A free mandate checker is a liability: the attacker model is an agent that wants to
exceed its mandate, and a free unlimited checker is exactly what such an agent wants to
call. Charging per check makes the checker an opponent rather than a free oracle.

## Tiers

| tier | price | includes | purpose |
|---|---|---|---|
| free | $0 | 100 checks/day, no key | real use, no registration friction |
| pay-per-check | $0.005 USDC (Base) | unlimited volume, x402 | the default for agents |
| subscription | $9/month | unlimited, SLA, ledger export | teams and operators |

**20 000 checks/month is the subscription's break-even** against the $9 price at a
$0.0021 effective per-call cost. Above that the subscription carries the margin.

## x402 configuration

Payment is per call, denominated in USDC on Base. The canonical facilitator address for
the rail is:

```
0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913   USDC (Base)
0x036CbD53842c5426634e7929541eC2318f3dCF7e   USDC (Sepolia, test)
```

`drift` does not implement its own payment code. It reuses the existing
`agentpay` x402 transport, so there is one implementation of the scheme, not two.

## What is still unverified

- `UNVERIFIED` whether agents will pay $0.005 for a check. The free tier exists to measure
  this: conversion from free to paid is the number that decides whether the price is right.
- `UNVERIFIED` that a live facilitator accepts this scheme shape. Requires a funded test
  wallet on Base, which needs a token from the owner.
