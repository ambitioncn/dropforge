from __future__ import annotations

import stat
import tempfile
import time
import unittest
from pathlib import Path

from dropforge.adapters.base import AdapterError
from dropforge.checkout import (
    CartLine,
    CartSnapshot,
    CheckoutLedger,
    CheckoutOutcome,
    CheckoutPolicyError,
    CheckoutSnapshot,
    GuardedCheckoutExecutor,
    PurchaseAuthorization,
    PurchaseIntent,
    validate_checkout,
)
from dropforge.checkout_browser import OpenClawCheckoutDriver


def intent(**overrides) -> PurchaseIntent:
    values = {
        "store": "https://shop.example.test",
        "product_title": "Exact Shoe",
        "variant_id": "v9",
        "variant_title": "US 9",
        "quantity": 1,
        "max_unit_price_cents": 15000,
        "max_total_cents": 20000,
        "candidate_key": "candidate-1",
        "currency": "USD",
    }
    values.update(overrides)
    result = PurchaseIntent(**values)
    result.validate()
    return result


def snapshot(**overrides) -> CheckoutSnapshot:
    values = {
        "cart": CartSnapshot(
            lines=(CartLine("Exact Shoe", "v9", "US 9", 1, 15000),),
            total_cents=15000,
            currency="USD",
        ),
        "final_total_cents": 17000,
        "challenge": None,
    }
    values.update(overrides)
    return CheckoutSnapshot(**values)


class FakeDriver:
    session_ref = "browser-tab-1"

    def __init__(self, *, view=None, outcome=None, error=None):
        self.view = view or snapshot()
        self.outcome = outcome or CheckoutOutcome("order_confirmed", "test-order")
        self.error = error
        self.submit_calls = 0

    def prepare(self, _intent):
        return self.view

    def inspect(self, _intent):
        return self.view

    def submit(self, _intent):
        self.submit_calls += 1
        if self.error:
            raise self.error
        return self.outcome

    def reconcile(self, _intent):
        return self.outcome


class FakeBrowserClient:
    timeout = 30

    def __init__(self):
        self.calls = []

    def _run(self, *args):
        self.calls.append(args)
        if args[0] == "focus" and not any(call[0] == "open" for call in self.calls):
            raise AdapterError("tab missing")
        if args[0] != "evaluate":
            return {"ok": True}
        script = args[-1]
        if "cart/clear.js" in script:
            result = {"dropforgeCheckoutVersion": 1, "status": "prepared"}
        elif "const buttons" in script:
            result = {"dropforgeCheckoutVersion": 1, "status": "clicked"}
        elif "thank_you" in script:
            result = {
                "dropforgeCheckoutVersion": 1,
                "status": "order_confirmed",
                "orderReference": "mock-order",
            }
        else:
            result = {
                "dropforgeCheckoutVersion": 1,
                "status": "ok",
                "challenge": None,
                "finalTotal": 17000,
                "currency": "USD",
                "cartTotal": 15000,
                "lines": [{
                    "productTitle": "Exact Shoe",
                    "variantId": "v9",
                    "variantTitle": "US 9",
                    "quantity": 1,
                    "unitPrice": 15000,
                }],
            }
        return {"result": result}


class IntentAndCartPolicyTests(unittest.TestCase):
    def test_intent_rejects_wildcards_quantity_and_impossible_ceiling(self):
        for change in (
            {"product_title": "Any"},
            {"variant_title": "All"},
            {"quantity": 0},
            {"quantity": 2, "max_total_cents": 20000},
            {"currency": ""},
        ):
            with self.assertRaises(ValueError):
                intent(**change)

    def test_cart_must_match_exactly(self):
        self.assertEqual(validate_checkout(snapshot(), intent()), 17000)
        bad_lines = (
            CartLine("Other Shoe", "v9", "US 9", 1, 15000),
            CartLine("Exact Shoe", "other", "US 9", 1, 15000),
            CartLine("Exact Shoe", "v9", "US 10", 1, 15000),
            CartLine("Exact Shoe", "v9", "US 9", 2, 15000),
            CartLine("Exact Shoe", "v9", "US 9", 1, 15001),
        )
        for bad_line in bad_lines:
            with self.subTest(line=bad_line), self.assertRaises(CheckoutPolicyError):
                validate_checkout(
                    snapshot(cart=CartSnapshot((bad_line,), 15000, "USD")), intent()
                )
        with self.assertRaises(CheckoutPolicyError):
            validate_checkout(snapshot(cart=CartSnapshot((), 0, "USD")), intent())
        with self.assertRaises(CheckoutPolicyError):
            validate_checkout(
                snapshot(cart=CartSnapshot(snapshot().cart.lines, 15000, "EUR")), intent()
            )

    def test_cart_and_final_total_ceilings_are_enforced(self):
        with self.assertRaises(CheckoutPolicyError):
            validate_checkout(snapshot(final_total_cents=20001), intent())
        with self.assertRaises(CheckoutPolicyError):
            validate_checkout(snapshot(final_total_cents=None), intent())


class AuthorizationTests(unittest.TestCase):
    def test_explicit_short_lived_authorization_is_required(self):
        purchase = intent()
        with self.assertRaises(CheckoutPolicyError):
            PurchaseAuthorization.issue(purchase, confirmed_total_cents=17000)
        authorization = PurchaseAuthorization.issue(
            purchase, confirmed_total_cents=17000, confirmed=True
        )
        authorization.validate(purchase, 17000, set())
        with self.assertRaises(CheckoutPolicyError):
            authorization.validate(purchase, 17000, {authorization.authorization_id})

    def test_expired_or_wrong_intent_authorization_is_rejected(self):
        purchase = intent()
        expired = PurchaseAuthorization("auth", purchase.fingerprint, 17000, time.time() - 1)
        with self.assertRaises(CheckoutPolicyError):
            expired.validate(purchase, 17000, set())
        wrong = PurchaseAuthorization("auth", "wrong", 17000, time.time() + 10)
        with self.assertRaises(CheckoutPolicyError):
            wrong.validate(purchase, 17000, set())


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.purchase = intent()

    def tearDown(self):
        self.temp.cleanup()

    def executor(self, driver):
        ledger = CheckoutLedger(Path(self.temp.name), self.purchase)
        return GuardedCheckoutExecutor(driver=driver, ledger=ledger), ledger

    def authorization(self):
        return PurchaseAuthorization.issue(
            self.purchase, confirmed_total_cents=17000, confirmed=True
        )

    def test_prepare_reaches_checkout_ready_and_ledger_is_private(self):
        executor, ledger = self.executor(FakeDriver())
        record = executor.prepare()
        self.assertEqual(record["state"], "checkout_ready")
        self.assertEqual(record["history"], ["created", "cart_verified", "checkout_ready"])
        self.assertEqual(stat.S_IMODE(ledger.path.stat().st_mode), 0o600)
        with self.assertRaises(CheckoutPolicyError):
            executor.prepare()
        with self.assertRaises(CheckoutPolicyError):
            ledger.transition("cancelled", card_number="forbidden")

    def test_executor_rejects_nonopaque_session_reference(self):
        driver = FakeDriver()
        driver.session_ref = "https://checkout.example/private"
        with self.assertRaises(CheckoutPolicyError):
            self.executor(driver)

    def test_challenge_preserves_same_session_for_handoff(self):
        driver = FakeDriver(view=snapshot(challenge="captcha"))
        executor, _ledger = self.executor(driver)
        record = executor.prepare()
        self.assertEqual(record["state"], "manual_auth_required")
        self.assertEqual(record["session_ref"], "browser-tab-1")
        self.assertEqual(record["challenge"], "captcha")
        driver.view = snapshot()
        resumed = executor.prepare()
        self.assertEqual(resumed["state"], "checkout_ready")
        self.assertEqual(resumed["session_ref"], "browser-tab-1")

    def test_submit_consumes_authorization_and_confirms_once(self):
        driver = FakeDriver()
        executor, _ledger = self.executor(driver)
        executor.prepare()
        record = executor.submit(self.authorization())
        self.assertEqual(record["state"], "order_confirmed")
        self.assertEqual(driver.submit_calls, 1)
        with self.assertRaises(CheckoutPolicyError):
            executor.submit(self.authorization())
        self.assertEqual(driver.submit_calls, 1)

    def test_unknown_submit_is_not_blindly_retried(self):
        driver = FakeDriver(error=TimeoutError())
        executor, ledger = self.executor(driver)
        executor.prepare()
        record = executor.submit(self.authorization())
        self.assertEqual(record["state"], "result_unknown")
        with self.assertRaises(CheckoutPolicyError):
            executor.submit(self.authorization())
        self.assertEqual(driver.submit_calls, 1)
        driver.error = None
        driver.outcome = CheckoutOutcome("order_confirmed", "reconciled-order")
        reconciled = executor.reconcile()
        self.assertEqual(reconciled["state"], "order_confirmed")
        self.assertEqual(ledger.read()["order_reference"], "reconciled-order")

    def test_post_submit_challenge_can_only_be_reconciled(self):
        driver = FakeDriver(outcome=CheckoutOutcome(
            "manual_auth_required", challenge="3ds"
        ))
        executor, _ledger = self.executor(driver)
        executor.prepare()
        handoff = executor.submit(self.authorization())
        self.assertEqual(handoff["state"], "manual_auth_required")
        self.assertEqual(handoff["stage"], "post_submit")
        with self.assertRaises(CheckoutPolicyError):
            executor.submit(self.authorization())
        self.assertEqual(driver.submit_calls, 1)
        driver.outcome = CheckoutOutcome("order_confirmed", "after-3ds")
        self.assertEqual(executor.reconcile()["state"], "order_confirmed")


class OpenClawCheckoutDriverTests(unittest.TestCase):
    def test_prepare_submit_and_session_label_contract(self):
        purchase = intent()
        client = FakeBrowserClient()
        driver = OpenClawCheckoutDriver(client, purchase)
        prepared = driver.prepare(purchase)
        self.assertEqual(validate_checkout(prepared, purchase), 17000)
        outcome = driver.submit(purchase)
        self.assertEqual(outcome.status, "order_confirmed")
        self.assertEqual(outcome.order_reference, "mock-order")
        self.assertTrue(driver.session_ref.startswith("dropforge-checkout-"))
        self.assertEqual(sum(call[0] == "open" for call in client.calls), 1)
        self.assertTrue(any(call[0] == "navigate" and call[1].endswith("/checkout") for call in client.calls))


if __name__ == "__main__":
    unittest.main()
