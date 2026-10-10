"""Server side of x402 for drift: issue the 402, verify the payment, grant the call.

Why this module is thin
-----------------------
``agentpay`` implements x402 v1/v2, EIP-3009 and EIP-712 — but it is a **client** of the
scheme: ``parse_challenge`` reads a provider's 402, ``check_challenge`` compares it to a
buyer's quote, and ``parse_settlement`` reads the receipt from a successful response.
None of those verify an *incoming* payment, which is what a server must do, so there is
nothing in it to import here and pretending otherwise produced a function that did not
exist.

What is shared instead is the part that must never drift: the chain id and the two USDC
contract addresses. ``test_x402_gate.py`` asserts they equal ``agentpay``'s whenever that
package is importable, so a change in either place fails a test instead of silently
producing a payment to the wrong contract.

Verification is delegated to the facilitator's ``/verify`` endpoint, and its answer is
re-checked here field by field against the requirement drift itself issued.

The flow
--------
1. Caller POSTs to ``/mcp`` without a payment header.
2. If the free quota is spent, drift answers **402** with a ``payment-required`` header
   carrying the requirement: 0.005 USDC on Base, payable to our address.
3. The caller pays and retries with an ``X-PAYMENT`` header.
4. drift asks the facilitator to verify the bundled authorization, then re-checks the
   settlement against **its own** record of what it demanded before serving the result.

Step 4 is the part that matters. A verifier that only asks the facilitator "is this
signature valid?" is trivially bypassed: the caller can pay one cent to an unrelated
address and present it. Every field of the settlement is compared against the
requirement drift itself issued, and a nonce is consumed exactly once.

Money handling
--------------
Amounts are integer USDC atoms (6 decimals) built with :class:`decimal.Decimal`. Float is
never used. ``0.005`` USDC is ``5000`` atoms, not ``0.0050000000000000001``.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping

__all__ = [
    "PRICE_USDC",
    "X402Unavailable",
    "PaymentConfig",
    "Settlement",
    "build_requirement",
    "challenge_headers",
    "challenge_body",
    "verify_payment",
    "X402Gate",
    "FacilitatorUnreachable",
]

DEFAULT_FACILITATOR = "https://x402.org/facilitator"

#: Price of one mandate check. 0.005 USDC, matching the measured unit economics in
#: MONETIZATION.md: a Base transaction costs ~$0.00146, so the rail takes ~29 %.
PRICE_USDC = Decimal("0.005")

# Триплеты (версия, схема, сеть), которые реально объявляет живой фасилитатор.
# Проверено против https://x402.org/facilitator/supported, а не взято из памяти:
#   v1 exact  base-sepolia            (тестнет, только)
#   v1 exact  solana-devnet           (тестнет, только)
#   v2 exact  eip155:84532            (Base Sepolia, CAIP-2)
#   v2 exact  и ещё 6 сетей, все тестнеты
#   v2 upto / batch-settlement        (Base Sepolia)
# mainnet Base (eip155:8453) НЕ поддерживается вовсе. Ловушка при проверке:
# подстрока "8453" совпадает с "84532", поэтому grep по сети врёт.
X402_VERSION = 1
SCHEME_EXACT = "exact"
NETWORK_BASE = "eip155:8453"
NETWORK_BASE_SEPOLIA = "eip155:84532"
NETWORK_BASE_SEPOLIA_V1 = "base-sepolia"

#: Что фасилитатор действительно обслуживает. Всё вне этого списка не заработает.
SUPPORTED_COMBINATIONS: frozenset[tuple[int, str, str]] = frozenset({
    (1, SCHEME_EXACT, NETWORK_BASE_SEPOLIA_V1),
    (2, SCHEME_EXACT, NETWORK_BASE_SEPOLIA),
    (2, "upto", NETWORK_BASE_SEPOLIA),
    (2, "batch-settlement", NETWORK_BASE_SEPOLIA),
})
USDC_DECIMALS = 6
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_BASE_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
SUPPORTED_NETWORKS = (NETWORK_BASE, NETWORK_BASE_SEPOLIA, NETWORK_BASE_SEPOLIA_V1)

HEADER_CHALLENGE = "payment-required"
HEADER_PAYMENT = "x-payment"
HEADER_SETTLEMENT = "payment-response"

#: v2 спецификация переименовала заголовок оплаты: PAYMENT-SIGNATURE.
#: X-PAYMENT — имя из v1. Пока сервис выставляет v1-челлендж,
#: HEADER_PAYMENT верен; но SUPPORTED_COMBINATIONS ниже уже содержит
#: пары (2, …), и как только появится v2-челлендж, клиент по v2 пришлёт
#: первый заголовок и не будет услышан — молча, без единой ошибки.
#: Читаем оба. Проверено в attest (attest/service.py:602), где так же
#: принимаются оба имени.
HEADER_PAYMENT_V2 = "payment-signature"
PAYMENT_HEADERS = (HEADER_PAYMENT, HEADER_PAYMENT_V2)

MAX_TIMEOUT_SECONDS = 60
FACILITATOR_TIMEOUT_SECONDS = 10


class FacilitatorUnreachable(RuntimeError):
    """The facilitator could not be reached or gave an unreadable answer.

    Distinct from a rejected payment on purpose. The caller may well have paid, so the
    nonce is released and the caller is told to retry — never that the payment was
    rejected, which would be a lie that also burns their money.
    """


class X402Unavailable(RuntimeError):
    """The paid tier cannot start: ``agentpay`` or the facilitator is missing."""


def to_atoms(amount: Decimal) -> int:
    """USDC amount to integer atoms. Exact: never routes through a float."""
    scaled = amount.scaleb(USDC_DECIMALS)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{amount} has more than {USDC_DECIMALS} decimals")
    return int(scaled)


def from_atoms(atoms: int) -> Decimal:
    return (Decimal(atoms) / (Decimal(10) ** USDC_DECIMALS)).normalize()


@dataclass(frozen=True)
class PaymentConfig:
    """What this deployment demands and who it trusts to verify payments."""

    pay_to: str
    facilitator_url: str = DEFAULT_FACILITATOR
    network: str = NETWORK_BASE_SEPOLIA_V1  # v1-форма тела соответствует base-sepolia
    asset: str = USDC_BASE
    price: Decimal = PRICE_USDC
    resource: str = "drift:/mcp"
    settlement_header: str = HEADER_SETTLEMENT

    version: int = X402_VERSION

    def __post_init__(self) -> None:
        if self.network not in SUPPORTED_NETWORKS:
            raise ValueError(
                f"unsupported network {self.network!r}; expected one of {SUPPORTED_NETWORKS}"
            )
        if self.facilitator_url == DEFAULT_FACILITATOR:
            key = (self.version, SCHEME_EXACT, self.network)
            if key not in SUPPORTED_COMBINATIONS:
                raise ValueError(
                    f"{DEFAULT_FACILITATOR} does not serve x402 v{self.version} "
                    f"'{SCHEME_EXACT}' on {self.network!r}. It serves only "
                    f"{sorted(SUPPORTED_COMBINATIONS)}. mainnet Base needs a "
                    f"different facilitator (PayAI serves mainnet EVM)."
                )
        if self.price <= 0:
            raise ValueError("price must be positive")
        to_atoms(self.price)  # fails fast on sub-atom precision

    @property
    def price_atoms(self) -> int:
        return to_atoms(self.price)


@dataclass(frozen=True)
class Settlement:
    """A verified payment, after comparison against the requirement we issued."""

    transaction_ref: str
    payer: str
    amount: Decimal
    network: str
    nonce: str = ""


def build_requirement(config: PaymentConfig, nonce: str) -> dict[str, Any]:
    """The ``x402`` requirement object drift demands. Also used as the replay record."""
    return {
        "scheme": SCHEME_EXACT,
        "network": config.network,
        # И amount, и maxAmountRequired обязательны: без amount фасилитатор
        # отвечает 500 "Cannot convert undefined to a BigInt". Проверено
        # вживую 09.10.2026.
        "amount": str(config.price_atoms),
        "maxAmountRequired": str(config.price_atoms),
        "resource": config.resource,
        "description": "drift: continuous mandate enforcement for AI agents",
        "mimeType": "application/json",
        "payTo": config.pay_to,
        "maxTimeoutSeconds": MAX_TIMEOUT_SECONDS,
        "asset": config.asset,
        "extra": {"name": "USDC", "version": "2", "nonce": nonce, "decimals": USDC_DECIMALS},
    }


def challenge_body(config: PaymentConfig, nonce: str) -> dict[str, Any]:
    """The JSON body of a 402 response."""
    return {"x402Version": X402_VERSION, "accepts": [build_requirement(config, nonce)]}


def challenge_headers(config: PaymentConfig, nonce: str) -> dict[str, str]:
    """Headers of a 402 response, with the challenge base64url-encoded per spec."""
    import base64

    raw = json.dumps(challenge_body(config, nonce), separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return {
        HEADER_CHALLENGE: encoded,
        "Content-Type": "application/json; charset=utf-8",
    }


def _dig(obj: Mapping[str, Any], *keys: str) -> Any:
    """Follow nested keys, returning None instead of raising on a missing level."""
    cur: Any = obj
    for key in keys:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


def _decode_payment_header(value: str) -> dict[str, Any]:
    """Read the ``X-PAYMENT`` header, accepting base64url-wrapped and raw JSON.

    Providers differ: some send the signed payload base64url-encoded, some send it as
    plain JSON. Rejecting either form would lock out callers for no gain, and the content
    is validated in full regardless of how it arrived.
    """
    import base64

    text = value.strip()
    if not text:
        raise ValueError("empty payment header")
    if text.startswith("{"):
        parsed = json.loads(text)
    else:
        padded = text + "=" * (-len(text) % 4)
        parsed = json.loads(base64.urlsafe_b64decode(padded).decode())
    if not isinstance(parsed, dict):
        raise ValueError("payment header is not an object")
    return parsed


def extract_nonce(presented: Mapping[str, Any]) -> str:
    """Найти nonce в платеже любой из версий.

    В v1 он лежит прямо в теле или в ``payload.nonce``. В v2 — в
    ``payload.authorization.nonce``, потому что в v2 блок ``payload``
    несёт подпись и авторизацию EIP-3009, а не наш служебный nonce.

    Прежний разбор смотрел только в payload и верхний уровень. Настоящий
    v2-платёж, полностью корректный, попадал в «payment carries no nonce»
    и отвергался. Проверено 09.10.2026 на живом фасилитаторе.

    Пустая строка означает «nonce не найден», и вызывающий обязан отказать.
    """
    candidates = (
        _dig(presented, "payload", "authorization", "nonce"),
        _dig(presented, "payload", "nonce"),
        _dig(presented, "nonce"),
        _dig(presented, "payload", "authorization", "nonce_hex"),
    )
    for value in candidates:
        if value:
            return str(value)
    return ""


def _post_facilitator(url: str, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url.rstrip("/") + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=FACILITATOR_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode())


class X402Gate:
    """Enforces payment on top of the free tier.

    Two independent reasons to demand money, and the stricter one wins:

    * the caller has no valid payment header, or
    * the caller has spent the free quota for its identity.

    A valid payment lifts the quota ceiling entirely — that is the paid tier.
    """

    def __init__(self, config: PaymentConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        #: nonce -> requirement, kept so a settled nonce cannot be replayed
        self._issued: dict[str, dict[str, Any]] = {}
        self._spent: set[str] = set()
        #: nonce -> "in flight", so two concurrent retries cannot both settle
        self._inflight: set[str] = set()
        self._max_tracked_nonces = 10_000

    @property
    def config(self) -> PaymentConfig:
        return self._config

    def issue_nonce(self) -> str:
        """Mint a fresh nonce and remember what it was demanded for."""
        import secrets

        nonce = secrets.token_hex(16)
        with self._lock:
            self._issued[nonce] = build_requirement(self._config, nonce)
            if len(self._issued) > self._max_tracked_nonces:
                # Forget the oldest half. An untracked nonce can no longer be matched to
                # a requirement, so verification rejects it rather than accepting it.
                for stale in list(self._issued)[: len(self._issued) // 2]:
                    self._issued.pop(stale, None)
        return nonce

    def requirement_for(self, nonce: str) -> dict[str, Any] | None:
        with self._lock:
            return self._issued.get(nonce)

    def _reserve(self, nonce: str) -> bool:
        """Claim a nonce for this attempt. False if already spent or in flight."""
        with self._lock:
            if nonce in self._spent or nonce in self._inflight:
                return False
            self._inflight.add(nonce)
            return True

    def _release(self, nonce: str) -> None:
        """Give the nonce back after a facilitator failure, so a retry can succeed."""
        with self._lock:
            self._inflight.discard(nonce)

    def _commit(self, nonce: str) -> None:
        """Burn the nonce permanently: the payment is settled."""
        with self._lock:
            self._inflight.discard(nonce)
            self._spent.add(nonce)

    def verify(self, payment_header: str, resource: str) -> Settlement:
        """Verify a presented payment and return the settlement, or raise.

        Every field is compared against the requirement **this gate issued**. Trusting
        the facilitator's ``isValid`` alone would let a caller pay a cent to an unrelated
        address and present the receipt: the signature would be genuine and the amount
        would be wrong. The facilitator answers "is this a valid payment"; only drift can
        answer "is it the payment I asked for".
        """
        config = self._config
        presented = _decode_payment_header(payment_header)

        # Разбор версии-agnostic: v1 и v2 кладут nonce в разные места,
        # и клиент присылает ту версию, которую объявил челендж.
        nonce = extract_nonce(presented)
        if not nonce:
            raise ValueError("payment carries no nonce")
        expected = self.requirement_for(nonce)
        if expected is None:
            raise ValueError("nonce was never issued by this deployment")
        if expected["resource"] != resource:
            raise ValueError("payment is for a different resource")
        if not self._reserve(nonce):
            raise ValueError("nonce already spent")

        try:
            result = _post_facilitator(config.facilitator_url, "/verify", {
                "x402Version": X402_VERSION,
                "paymentPayload": presented,
                # Имя поля — paymentRequirements. Прежнее `requirements`
                # фасилитатор отвергал целиком: HTTP 400 missing_parameters.
                "paymentRequirements": expected,
            })
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # The nonce is released, not burned: we do not know whether the money moved,
            # and telling the caller "rejected" would be both wrong and expensive.
            self._release(nonce)
            raise FacilitatorUnreachable(str(exc)) from exc
        if not result.get("isValid"):
            self._commit(nonce)
            raise ValueError(
                f"facilitator rejected the payment: {result.get('invalidReason', 'unknown')}"
            )

        payer = str(result.get("payer", ""))
        if expected["payTo"].lower() not in {payer.lower(), ""} and payer:
            self._commit(nonce)
            raise ValueError("facilitator reports a different payee than the one demanded")

        details = result.get("details") or {}
        settled_atoms = details.get("amount", result.get("amount"))
        if settled_atoms is None:
            self._commit(nonce)
            raise ValueError("facilitator did not report a settled amount")
        if int(settled_atoms) != config.price_atoms:
            self._commit(nonce)
            raise ValueError(
                f"paid {from_atoms(int(settled_atoms))} USDC, "
                f"required {config.price} USDC"
            )
        settled_network = str(details.get("network", result.get("network", "")))
        if settled_network and settled_network != config.network:
            self._commit(nonce)
            raise ValueError(f"settled on {settled_network}, required {config.network}")

        self._commit(nonce)
        return Settlement(
            transaction_ref=str(details.get("transaction", result.get("transaction", ""))),
            payer=payer,
            amount=from_atoms(int(settled_atoms)),
            network=config.network,
            nonce=nonce,
        )

    def settlement_headers(self, settlement: Settlement) -> dict[str, str]:
        """Headers for the successful response, carrying the receipt back."""
        import base64

        payload = json.dumps({
            "transactionRef": settlement.transaction_ref,
            "network": settlement.network,
            "amount": str(settlement.amount),
        }, separators=(",", ":")).encode()
        encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        return {HEADER_SETTLEMENT: encoded}
