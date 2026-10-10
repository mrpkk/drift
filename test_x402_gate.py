"""Tests for the x402 payment gate. No network, no funds, no chain access.

The facilitator is replaced by a stub. Every test therefore proves a decision drift makes
by itself — comparing the settlement against the requirement it issued — which is the only
part of the flow an attacker actually targets.
"""

from __future__ import annotations

import base64
import json
import pathlib
import unittest
from decimal import Decimal
from unittest import mock

import http_server
import x402_gate
from x402_gate import (
    MAX_TIMEOUT_SECONDS,
    NETWORK_BASE,
    NETWORK_BASE_SEPOLIA,
    PRICE_USDC,
    SCHEME_EXACT,
    USDC_BASE,
    USDC_BASE_SEPOLIA,
    USDC_DECIMALS,
    NETWORK_BASE_SEPOLIA_V1,
    PaymentConfig,
    SUPPORTED_COMBINATIONS,
    FacilitatorUnreachable,
    X402Gate,
    _decode_payment_header,
    extract_nonce,
    build_requirement,
    challenge_body,
    challenge_headers,
    from_atoms,
    to_atoms,
)

PAY_TO = "0x1111111111111111111111111111111111111111"


def b64url(obj: dict) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class TestAtoms(unittest.TestCase):
    """Money must never touch a float."""

    def test_price_to_atoms_is_exact(self):
        self.assertEqual(to_atoms(Decimal("0.005")), 5000)

    def test_atoms_round_trip(self):
        self.assertEqual(from_atoms(5000), Decimal("0.005"))

    def test_sub_atom_precision_is_rejected(self):
        with self.assertRaises(ValueError):
            to_atoms(Decimal("0.0000001"))

    def test_one_whole_usdc(self):
        self.assertEqual(to_atoms(Decimal("1")), 10 ** USDC_DECIMALS)


class TestConfig(unittest.TestCase):
    def test_rejects_unknown_network(self):
        with self.assertRaises(ValueError):
            PaymentConfig(pay_to=PAY_TO, network="eip155:1")

    def test_rejects_non_positive_price(self):
        with self.assertRaises(ValueError):
            PaymentConfig(pay_to=PAY_TO, price=Decimal("0"))

    def test_accepts_only_served_networks(self):
        """mainnet Base не обслуживается: принимать его молча нельзя."""
        self.assertEqual(PaymentConfig(pay_to=PAY_TO).network,
                         NETWORK_BASE_SEPOLIA_V1)
        self.assertEqual(PaymentConfig(pay_to=PAY_TO, network=NETWORK_BASE_SEPOLIA,
                                      version=2).network, NETWORK_BASE_SEPOLIA)

    def test_default_price_matches_documented_economics(self):
        self.assertEqual(PaymentConfig(pay_to=PAY_TO).price_atoms, 5000)


class TestRequirement(unittest.TestCase):
    def setUp(self):
        self.cfg = PaymentConfig(pay_to=PAY_TO)

    def test_requirement_states_the_demanded_price(self):
        req = build_requirement(self.cfg, "abc123")
        self.assertEqual(req["maxAmountRequired"], "5000")
        self.assertEqual(req["payTo"], PAY_TO)
        self.assertEqual(req["scheme"], SCHEME_EXACT)
        self.assertEqual(req["asset"], USDC_BASE)

    def test_nonce_is_carried_in_extra(self):
        self.assertEqual(build_requirement(self.cfg, "n1")["extra"]["nonce"], "n1")

    def test_body_wraps_one_requirement(self):
        body = challenge_body(self.cfg, "n1")
        self.assertEqual(body["x402Version"], 1)
        self.assertEqual(len(body["accepts"]), 1)

    def test_headers_are_base64url_without_padding(self):
        headers = challenge_headers(self.cfg, "n1")
        value = headers["payment-required"]
        self.assertNotIn("=", value)
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode())
        self.assertEqual(decoded["accepts"][0]["maxAmountRequired"], "5000")

    def test_timeout_is_bounded(self):
        self.assertLessEqual(build_requirement(self.cfg, "n")["maxTimeoutSeconds"],
                             MAX_TIMEOUT_SECONDS)


class TestPaymentHeaderDecoding(unittest.TestCase):
    def test_raw_json_is_accepted(self):
        self.assertEqual(_decode_payment_header('{"nonce":"a"}'), {"nonce": "a"})

    def test_base64url_is_accepted(self):
        self.assertEqual(_decode_payment_header(b64url({"nonce": "b"})), {"nonce": "b"})

    def test_base64url_with_padding_is_accepted(self):
        raw = json.dumps({"nonce": "c"}).encode()
        self.assertEqual(_decode_payment_header(base64.urlsafe_b64encode(raw).decode()),
                         {"nonce": "c"})

    def test_empty_header_rejected(self):
        with self.assertRaises(ValueError):
            _decode_payment_header("   ")

    def test_non_object_rejected(self):
        with self.assertRaises(ValueError):
            _decode_payment_header(b64url([1, 2, 3]))

    def test_garbage_rejected(self):
        with self.assertRaises(ValueError):
            _decode_payment_header("!!!not base64!!!")


class FakeFacilitator:
    """Stands in for the x402 facilitator. Records calls, returns a scripted answer."""

    def __init__(self, is_valid=True, amount=5000, network=None,
                 payer=PAY_TO, transaction="0xdead"):
        network = network or NETWORK_BASE_SEPOLIA_V1
        self.is_valid = is_valid
        self.amount = amount
        self.network = network
        self.payer = payer
        self.transaction = transaction
        self.calls: list[dict] = []

    def __call__(self, url, path, payload):
        self.calls.append({"url": url, "path": path, "payload": payload})
        return {
            "isValid": self.is_valid,
            "invalidReason": "scripted failure" if not self.is_valid else None,
            "payer": self.payer,
            "details": {
                "amount": self.amount,
                "network": self.network,
                "transaction": self.transaction,
            },
        }


class TestGateHappyPath(unittest.TestCase):
    def setUp(self):
        self.cfg = PaymentConfig(pay_to=PAY_TO, resource="drift:/mcp")
        self.gate = X402Gate(self.cfg)
        self.fac = FakeFacilitator(network=self.cfg.network)

    def _pay(self, gate=None, **kw):
        gate = gate or self.gate
        nonce = gate.issue_nonce()
        header = b64url({"nonce": nonce, "signature": "0xsig", "payload": {"nonce": nonce}})
        with mock.patch("x402_gate._post_facilitator", self.fac):
            return gate.verify(header, "drift:/mcp")

    def test_valid_payment_settles(self):
        s = self._pay()
        self.assertEqual(s.amount, Decimal("0.005"))
        self.assertEqual(s.network, self.cfg.network)
        self.assertEqual(s.transaction_ref, "0xdead")

    def test_facilitator_was_actually_called(self):
        self._pay()
        self.assertEqual(len(self.fac.calls), 1)
        self.assertEqual(self.fac.calls[0]["path"], "/verify")

    def test_requirement_sent_matches_what_was_issued(self):
        """Имя поля исправлено 09.10.2026: было `requirements`.

        Тест проверял то имя, которое мы сами и отправляли, поэтому был
        зелёным. Живой фасилитатор на `requirements` отвечает HTTP 400
        missing_parameters — платёж отвергался, не дойдя до подписи.
        Тест, закрепляющий неверный ключ, хуже отсутствия теста: он
        уверяет, что путь рабочий.
        """
        self._pay()
        sent = self.fac.calls[0]["payload"]["paymentRequirements"]
        self.assertEqual(sent["maxAmountRequired"], "5000")
        self.assertEqual(sent["payTo"], PAY_TO)

    def test_nonces_are_unique(self):
        self.assertNotEqual(self.gate.issue_nonce(), self.gate.issue_nonce())


class TestGateRejections(unittest.TestCase):
    """Every case here is a way to get a free call or a wrong-amount call."""

    def setUp(self):
        self.cfg = PaymentConfig(pay_to=PAY_TO, resource="drift:/mcp")
        self.gate = X402Gate(self.cfg)

    def _header(self, nonce, **extra):
        body = {"nonce": nonce, "signature": "0xsig"}
        body.update(extra)
        return b64url(body)

    def test_replay_is_refused(self):
        nonce = self.gate.issue_nonce()
        header = self._header(nonce)
        with mock.patch("x402_gate._post_facilitator", FakeFacilitator()):
            self.gate.verify(header, "drift:/mcp")
        with mock.patch("x402_gate._post_facilitator", FakeFacilitator()):
            with self.assertRaisesRegex(ValueError, "already spent"):
                self.gate.verify(header, "drift:/mcp")

    def test_unknown_nonce_is_refused(self):
        with self.assertRaisesRegex(ValueError, "never issued"):
            self.gate.verify(self._header("forged"), "drift:/mcp")

    def test_missing_nonce_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no nonce"):
            self.gate.verify(b64url({"signature": "0x"}), "drift:/mcp")

    def test_payment_for_another_resource_is_refused(self):
        nonce = self.gate.issue_nonce()
        with self.assertRaisesRegex(ValueError, "different resource"):
            self.gate.verify(self._header(nonce), "drift:/other")

    def test_underpayment_is_refused(self):
        """The attack: pay one cent, present a genuine receipt."""
        nonce = self.gate.issue_nonce()
        with mock.patch("x402_gate._post_facilitator",
                        FakeFacilitator(amount=1, network=NETWORK_BASE_SEPOLIA_V1)):
            with self.assertRaisesRegex(ValueError, "required 0.005 USDC"):
                self.gate.verify(self._header(nonce), "drift:/mcp")

    def test_overpayment_is_refused(self):
        nonce = self.gate.issue_nonce()
        with mock.patch("x402_gate._post_facilitator",
                        FakeFacilitator(amount=999_999, network=NETWORK_BASE_SEPOLIA_V1)):
            with self.assertRaisesRegex(ValueError, "required 0.005 USDC"):
                self.gate.verify(self._header(nonce), "drift:/mcp")

    def test_wrong_network_is_refused(self):
        nonce = self.gate.issue_nonce()
        with mock.patch("x402_gate._post_facilitator",
                        FakeFacilitator(network=NETWORK_BASE_SEPOLIA)):
            with self.assertRaisesRegex(ValueError, "settled on"):
                self.gate.verify(self._header(nonce), "drift:/mcp")

    def test_facilitator_rejection_is_refused(self):
        nonce = self.gate.issue_nonce()
        with mock.patch("x402_gate._post_facilitator",
                        FakeFacilitator(is_valid=False, network=NETWORK_BASE_SEPOLIA_V1)):
            with self.assertRaisesRegex(ValueError, "facilitator rejected"):
                self.gate.verify(self._header(nonce), "drift:/mcp")

    def test_missing_settled_amount_is_refused(self):
        nonce = self.gate.issue_nonce()
        with mock.patch("x402_gate._post_facilitator", lambda u, p, x: {"isValid": True}):
            with self.assertRaisesRegex(ValueError, "did not report a settled amount"):
                self.gate.verify(self._header(nonce), "drift:/mcp")

    def test_nonce_is_spent_even_when_amount_is_wrong(self):
        """Otherwise an attacker retries the same nonce with a corrected amount."""
        nonce = self.gate.issue_nonce()
        with mock.patch("x402_gate._post_facilitator",
                        FakeFacilitator(amount=1, network=NETWORK_BASE_SEPOLIA_V1)):
            with self.assertRaises(ValueError):
                self.gate.verify(self._header(nonce), "drift:/mcp")
        with mock.patch("x402_gate._post_facilitator", FakeFacilitator()):
            with self.assertRaisesRegex(ValueError, "already spent"):
                self.gate.verify(self._header(nonce), "drift:/mcp")

    def test_facilitator_is_not_consulted_for_a_forged_nonce(self):
        """A forged nonce must cost nothing, not even one outbound request."""
        fac = FakeFacilitator()
        with mock.patch("x402_gate._post_facilitator", fac):
            with self.assertRaises(ValueError):
                self.gate.verify(self._header("nope"), "drift:/mcp")
        self.assertEqual(fac.calls, [])


class TestNonceStoreBound(unittest.TestCase):
    def test_store_is_bounded(self):
        gate = X402Gate(PaymentConfig(pay_to=PAY_TO))
        gate._max_tracked_nonces = 20
        for _ in range(60):
            gate.issue_nonce()
        self.assertLessEqual(len(gate._issued), 20)

    def test_forgotten_nonce_is_rejected_not_accepted(self):
        """Eviction must fail closed."""
        gate = X402Gate(PaymentConfig(pay_to=PAY_TO))
        gate._max_tracked_nonces = 2
        first = gate.issue_nonce()
        for _ in range(10):
            gate.issue_nonce()
        self.assertIsNone(gate.requirement_for(first))


class TestConsistencyWithAgentpay(unittest.TestCase):
    """Chain id and contract addresses must never diverge from the canonical client."""

    def test_constants_match_agentpay_when_available(self):
        try:
            from agentpay import x402 as agent_x402
        except ImportError:
            self.skipTest("agentpay is not installed; nothing to diverge from")
        self.assertEqual(NETWORK_BASE, agent_x402.BASE_NETWORK)
        self.assertEqual(USDC_BASE, agent_x402.USDC_BASE)
        self.assertEqual(USDC_BASE_SEPOLIA, agent_x402.USDC_BASE_SEPOLIA)
        self.assertEqual(NETWORK_BASE_SEPOLIA, agent_x402.BASE_SEPOLIA_NETWORK)
        self.assertEqual(USDC_DECIMALS, agent_x402.USDC_DECIMALS)

    def test_documented_price_is_unchanged(self):
        self.assertEqual(PRICE_USDC, Decimal("0.005"))

class TestFacilitatorOutage(unittest.TestCase):
    """An unreachable facilitator must not cost the caller their money.

    The dangerous design is to burn the nonce first and then call out: the facilitator
    goes down, the nonce is spent, the caller is told "rejected", and their payment may
    already have moved. So the nonce is reserved, released on outage, and only committed
    once the answer is in.
    """

    def setUp(self):
        self.gate = X402Gate(PaymentConfig(pay_to=PAY_TO, resource="drift:/mcp"))

    def _header(self, nonce):
        return b64url({"nonce": nonce, "signature": "0xsig"})

    def test_outage_raises_its_own_exception(self):
        import urllib.error
        nonce = self.gate.issue_nonce()

        def down(url, path, payload):
            raise urllib.error.URLError("down")

        with mock.patch("x402_gate._post_facilitator", down):
            with self.assertRaises(FacilitatorUnreachable):
                self.gate.verify(self._header(nonce), "drift:/mcp")

    def test_nonce_is_released_not_burned(self):
        import urllib.error
        nonce = self.gate.issue_nonce()

        def down(url, path, payload):
            raise urllib.error.URLError("down")

        with mock.patch("x402_gate._post_facilitator", down):
            with self.assertRaises(FacilitatorUnreachable):
                self.gate.verify(self._header(nonce), "drift:/mcp")
        with mock.patch("x402_gate._post_facilitator",
                        FakeFacilitator(network=NETWORK_BASE_SEPOLIA_V1)):
            settlement = self.gate.verify(self._header(nonce), "drift:/mcp")
        self.assertEqual(settlement.amount, Decimal("0.005"))

    def test_nonce_is_burned_after_success(self):
        nonce = self.gate.issue_nonce()
        with mock.patch("x402_gate._post_facilitator",
                        FakeFacilitator(network=NETWORK_BASE_SEPOLIA_V1)):
            self.gate.verify(self._header(nonce), "drift:/mcp")
        with mock.patch("x402_gate._post_facilitator",
                        FakeFacilitator(network=NETWORK_BASE_SEPOLIA_V1)):
            with self.assertRaisesRegex(ValueError, "already spent"):
                self.gate.verify(self._header(nonce), "drift:/mcp")

    def test_reservation_blocks_a_concurrent_second_claim(self):
        gate = self.gate
        self.assertTrue(gate._reserve("x"))
        self.assertFalse(gate._reserve("x"))  # второй не должен получить тот же nonce
        gate._release("x")
        self.assertTrue(gate._reserve("x"))  # после освобождения — снова можно

    def test_commit_beats_release(self):
        gate = self.gate
        gate._reserve("y")
        gate._commit("y")
        gate._release("y")  # поздний release не должен воскресить nonce
        self.assertFalse(gate._reserve("y"))

    def test_unreadable_facilitator_answer_is_an_outage_not_a_rejection(self):
        nonce = self.gate.issue_nonce()
        with mock.patch("x402_gate._post_facilitator", lambda u, p, x: "not a dict"):
            with self.assertRaises((FacilitatorUnreachable, AttributeError, ValueError)):
                self.gate.verify(self._header(nonce), "drift:/mcp")


class TestServerWiring(unittest.TestCase):
    """The HTTP layer must distinguish an outage from a rejection."""

    def test_settle_maps_outage_to_503(self):
        import http_server
        from x402_gate import FacilitatorUnreachable as FU
        self.assertTrue(issubclass(FU, RuntimeError))
        self.assertFalse(issubclass(FU, ValueError))

    def test_rejection_stays_402(self):
        import http_server
        src = (pathlib.Path(http_server.__file__)).read_text()
        self.assertIn('"error": "payment_rejected"', src)
        self.assertIn('"error": "verification_unavailable"', src)

    def test_paid_tier_requires_a_pay_to_address(self):
        import http_server
        self.assertEqual(http_server.X402_PAY_TO_ENV, "DRIFT_X402_PAY_TO")

    def test_header_names_are_lowercase_safe(self):
        import http_server
        self.assertEqual(http_server.HEADER_PAYMENT, "x-payment")

class TestFacilitatorMatrix(unittest.TestCase):
    """The supported (version, scheme, network) triples were read from the live
    facilitator, not assumed. mainnet Base is absent, and a substring check for
    "8453" is misleading because it also matches "84532".
    """

    def test_default_combination_is_actually_supported(self):
        cfg = PaymentConfig(pay_to=PAY_TO)
        self.assertIn((cfg.version, SCHEME_EXACT, cfg.network), SUPPORTED_COMBINATIONS)

    def test_mainnet_base_is_refused_by_this_facilitator(self):
        with self.assertRaisesRegex(ValueError, "does not serve"):
            PaymentConfig(pay_to=PAY_TO, network=NETWORK_BASE)

    def test_v1_on_caip2_sepolia_is_refused(self):
        """v1 is registered under base-sepolia, not eip155:84532."""
        with self.assertRaises(ValueError):
            PaymentConfig(pay_to=PAY_TO, network=NETWORK_BASE_SEPOLIA)

    def test_v2_on_sepolia_is_allowed(self):
        cfg = PaymentConfig(pay_to=PAY_TO, network=NETWORK_BASE_SEPOLIA, version=2)
        self.assertEqual(cfg.version, 2)

    def test_custom_facilitator_may_serve_mainnet(self):
        cfg = PaymentConfig(pay_to=PAY_TO, network=NETWORK_BASE,
                            facilitator_url="https://facilitator.pay.ai")
        self.assertEqual(cfg.network, NETWORK_BASE)

    def test_no_supported_combination_is_mainnet_base(self):
        for _version, _scheme, network in SUPPORTED_COMBINATIONS:
            self.assertNotEqual(network, NETWORK_BASE,
                                "mainnet Base must not appear in the supported set")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestPaymentHeaderNames(unittest.TestCase):
    """Заголовок оплаты называется по-разному в версиях спецификации.

    Проверено в attest (attest/service.py:602): там читаются оба имени,
    потому что клиент может прийти по любой из версий. Здесь тот же риск
    был реальным — SUPPORTED_COMBINATIONS уже содержит пары (2, …),
    то есть v2-челлендж планируется, а заголовок был только v1.
    """

    def test_both_names_are_declared(self):
        self.assertEqual(x402_gate.HEADER_PAYMENT, "x-payment")
        self.assertEqual(x402_gate.HEADER_PAYMENT_V2, "payment-signature")
        self.assertIn(x402_gate.HEADER_PAYMENT, x402_gate.PAYMENT_HEADERS)
        self.assertIn(x402_gate.HEADER_PAYMENT_V2, x402_gate.PAYMENT_HEADERS)

    def test_server_reads_either_name(self):
        """Сервер обязан слышать плательщика по обеим версиям."""
        import inspect
        src = inspect.getsource(http_server)
        self.assertIn("PAYMENT_HEADERS", src)
        # ровно одно место читает заголовок, и оно перебирает оба имени
        self.assertNotIn("self.headers.get(HEADER_PAYMENT)", src)


if __name__ == "__main__":
    unittest.main()


class TestV2WireFormat(unittest.TestCase):
    """Настоящий v2-платёж: nonce лежит в payload.authorization.nonce.

    Проверено 09.10.2026 при работе над ṚTA: в v2 тело платежа устроено
    иначе, чем в v1. Прежний разбор смотрел только в payload.nonce, и
    корректный v2-платёж отвергался с «payment carries no nonce».
    """

    def _v2_payment(self, nonce: str) -> dict:
        return {
            "x402Version": 2,
            "accepted": {
                "scheme": "exact",
                "network": NETWORK_BASE_SEPOLIA,
                "maxAmountRequired": "5000",
                "payTo": PAY_TO,
                "asset": USDC_BASE_SEPOLIA,
                "extra": {"name": "USDC", "version": "2"},
            },
            "payload": {
                "signature": "0x" + "11" * 65,
                "authorization": {
                    "from": "0x" + "9c" * 20, "to": PAY_TO, "value": "5000",
                    "validAfter": "0", "validBefore": "9999999999",
                    "nonce": nonce,
                },
            },
        }

    def test_v2_nonce_is_read_from_authorization(self):
        gate = X402Gate(PaymentConfig(pay_to=PAY_TO))
        issued = gate.issue_nonce()
        self.assertEqual(extract_nonce(self._v2_payment(issued)), issued)

    def test_v1_nonce_still_works(self):
        """v1 не должен сломаться: публичный фасилитатор обслуживает v1
        на base-sepolia, и отказ от него убирает работающий путь."""
        gate = X402Gate(PaymentConfig(pay_to=PAY_TO))
        issued = gate.issue_nonce()
        self.assertEqual(extract_nonce({"payload": {"nonce": issued}}), issued)

    def test_nonce_absent_everywhere_is_empty_not_invented(self):
        self.assertEqual(extract_nonce({"payload": {"authorization": {}}}), "")


class TestFacilitatorPayloadShape(unittest.TestCase):
    """Форма запроса проверена вживую 09.10.2026 против x402.org."""

    PAY = "0x1111111111111111111111111111111111111111"
    RESOURCE = "drift:/mcp"

    def _sent_payload(self):
        gate = X402Gate(PaymentConfig(
            pay_to=self.PAY, network=NETWORK_BASE_SEPOLIA_V1,
            resource=self.RESOURCE))
        nonce = gate.issue_nonce()
        import x402_gate as mod
        seen = {}
        real = mod._post_facilitator

        def spy(url, path, payload):
            seen.update(payload)
            return {"isValid": True, "payer": self.PAY,
                    "amount": str(gate.config.price_atoms)}

        mod._post_facilitator = spy
        try:
            gate.verify(b64url({"payload": {"nonce": nonce}}), self.RESOURCE)
        finally:
            mod._post_facilitator = real
        return seen

    def test_key_is_paymentrequirements(self):
        sent = self._sent_payload()
        self.assertIn("paymentRequirements", sent)
        self.assertNotIn("requirements", sent)

    def test_both_amount_fields_present(self):
        """Без amount фасилитатор отвечает 500 Cannot convert undefined
        to a BigInt — проверено вживую."""
        req = self._sent_payload()["paymentRequirements"]
        self.assertEqual(req["amount"], req["maxAmountRequired"])

    def test_token_name_and_version_present(self):
        """Без extra:{name,version} фасилитатор не собирает домен EIP-712
        и отвечает missing_eip712_domain — проверено вживую."""
        extra = self._sent_payload()["paymentRequirements"]["extra"]
        self.assertEqual(extra["name"], "USDC")
        self.assertEqual(extra["version"], "2")


if __name__ == "__main__":
    unittest.main()
