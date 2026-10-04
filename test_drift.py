"""Тесты drift. Запуск: python3 test_drift.py"""
import unittest

from drift import Action, Drift, Mandate, check

T0 = 1_700_000_000


def mandate(**kw):
    base = dict(payee_id="shop", audience="merchant.example",
                max_amount_minor=10_000, currency="USD",
                valid_from=T0, valid_until=T0 + 86_400)
    base.update(kw)
    return Mandate(**base)


def action(**kw):
    base = dict(intent="buy", payee_id="shop", audience="merchant.example",
                amount_minor=100, currency="USD", at=T0 + 60)
    base.update(kw)
    return Action(**base)


class TestRules(unittest.TestCase):
    def test_allows_conforming_action(self):
        self.assertEqual(check(mandate(), action()), ("ALLOW", "-"))

    def test_amount_is_the_first_rule(self):
        """Проверка суммы идёт первой: превышение лимита важнее прочего."""
        v, r = check(mandate(), action(payee_id="wrong", amount_minor=10_001))
        self.assertEqual(r, "amount")

    def test_amount_equal_to_limit_is_allowed(self):
        self.assertEqual(check(mandate(), action(amount_minor=10_000))[0], "ALLOW")

    def test_amount_one_over_is_denied(self):
        self.assertEqual(check(mandate(), action(amount_minor=10_001))[0], "DENY")

    def test_payee_mismatch(self):
        self.assertEqual(check(mandate(), action(payee_id="other"))[1], "payee")

    def test_audience_mismatch(self):
        """Тот самый класс дефекта, что был в SDK: аудитория обязана сверяться."""
        self.assertEqual(check(mandate(), action(audience="attacker.example"))[1], "audience")

    def test_currency_mismatch(self):
        self.assertEqual(check(mandate(), action(currency="BRL"))[1], "currency")

    def test_before_window(self):
        self.assertEqual(check(mandate(), action(at=T0 - 1))[1], "window")

    def test_after_window(self):
        self.assertEqual(check(mandate(), action(at=T0 + 86_401))[1], "window")

    def test_window_edges_are_inclusive(self):
        m = mandate()
        self.assertEqual(check(m, action(at=m.valid_from))[0], "ALLOW")
        self.assertEqual(check(m, action(at=m.valid_until))[0], "ALLOW")

    def test_intent_not_in_allowlist(self):
        self.assertEqual(check(mandate(allowed_intents=("buy",)), action(intent="sell"))[1], "intent")

    def test_empty_allowlist_means_any_intent(self):
        self.assertEqual(check(mandate(), action(intent="anything"))[0], "ALLOW")

    def test_intent_in_allowlist_is_allowed(self):
        self.assertEqual(check(mandate(allowed_intents=("buy",)), action())[0], "ALLOW")


class TestDenyByDefault(unittest.TestCase):
    def test_every_field_is_checked(self):
        """Каждое поле сверяется: не должно быть способа проскочить."""
        m = mandate(allowed_intents=("buy",))
        cases = {
            "payee": action(payee_id="x"), "audience": action(audience="x"),
            "currency": action(currency="x"), "window": action(at=0),
            "amount": action(amount_minor=10_001), "intent": action(intent="x"),
        }
        for rule, a in cases.items():
            with self.subTest(rule=rule):
                self.assertEqual(check(m, a), ("DENY", rule))


class TestLedger(unittest.TestCase):
    def test_append_only_records_everything(self):
        d = Drift(mandate())
        d.submit(action())
        d.submit(action(amount_minor=99_999))
        self.assertEqual(len(d.ledger.rows), 2)

    def test_denied_lists_only_denials(self):
        d = Drift(mandate())
        d.submit(action())
        d.submit(action(audience="attacker.example"))
        self.assertEqual(len(d.ledger.denied()), 1)

    def test_fingerprint_is_stable_for_same_history(self):
        a, b = Drift(mandate()), Drift(mandate())
        for d in (a, b):
            d.submit(action())
        self.assertEqual(a.ledger.fingerprint(), b.ledger.fingerprint())

    def test_fingerprint_changes_when_history_changes(self):
        """Доказательство нельзя переписать незаметно."""
        a, b = Drift(mandate()), Drift(mandate())
        a.submit(action())
        b.submit(action(amount_minor=99_999))
        self.assertNotEqual(a.ledger.fingerprint(), b.ledger.fingerprint())

    def test_fingerprint_changes_on_reorder(self):
        a, b = Drift(mandate()), Drift(mandate())
        a.submit(action(amount_minor=1))
        a.submit(action(amount_minor=2))
        b.submit(action(amount_minor=2))
        b.submit(action(amount_minor=1))
        self.assertNotEqual(a.ledger.fingerprint(), b.ledger.fingerprint())

    def test_report_breach_by_rule(self):
        d = Drift(mandate())
        d.submit(action())
        d.submit(action(amount_minor=99_999))
        d.submit(action(amount_minor=98_999))
        d.submit(action(payee_id="x"))
        r = d.report()
        self.assertEqual((r["submitted"], r["allowed"], r["denied"]), (4, 1, 3))
        self.assertEqual(r["breach_by_rule"], {"amount": 2, "payee": 1})


class TestMandateIsImmutable(unittest.TestCase):
    def test_cannot_mutate_in_place(self):
        m = mandate()
        with self.assertRaises(Exception):
            m.max_amount_minor = 999_999


if __name__ == "__main__":
    unittest.main(verbosity=2)
