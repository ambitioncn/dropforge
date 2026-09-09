from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from dropforge.autobuy import AutoPurchaseCoordinator, DropbotCommandRunner, PurchaseRunResult
from dropforge.models import (
    Candidate,
    DropTarget,
    MatchRule,
    Observation,
    Product,
    StandingPurchasePolicy,
    Variant,
)
from dropforge.notifications import OpenClawMessageSink
from dropforge.state import StateStore


def policy() -> StandingPurchasePolicy:
    return StandingPurchasePolicy(
        sizes=("9", "9.5", "10", "8.5", "8", "10.5"),
        quantity_per_product=3,
        max_all_in_per_unit_cents=30000,
        currency="USD",
        runner_path="/opt/dropbot",
        secret_file="/run/secrets/checkout",
    )


def target() -> DropTarget:
    return DropTarget(
        id="travis-shoes",
        adapter="fake",
        store="https://shop.example.test",
        query="shoe",
        interval_seconds=5,
        match=MatchRule(
            title_any_contains=("jordan", "shoe"),
            sizes=("8", "8.5", "9", "9.5", "10", "10.5"),
            max_unit_price_cents=30000,
        ),
        purchase=policy(),
    )


def observation() -> Observation:
    product = Product(
        id="shoe-1",
        title="Cactus Jack Jordan Shoe",
        handle="jordan-shoe",
        url="https://shop.example.test/products/jordan-shoe",
        variants=(
            Variant("v10", "US 10", True, 20000, ("10",)),
            Variant("v9", "US 9", True, 20000, ("9",)),
        ),
    )
    return Observation(
        "travis-shoes",
        "available",
        1.0,
        tuple(Candidate("travis-shoes", "https://shop.example.test", product, v) for v in product.variants),
    )


class FakeRunner:
    def __init__(self, root: Path, *, prepare=None, submit=None, reconcile=None):
        self.root = root
        self.prepare_result = prepare or PurchaseRunResult("checkout_ready", "submit")
        self.submit_result = submit or PurchaseRunResult("order_confirmed", order_reference="order-1")
        self.reconcile_result = reconcile or PurchaseRunResult("order_confirmed", order_reference="order-1")
        self.prepare_calls = 0
        self.submit_calls = 0
        self.reconcile_calls = 0
        self.selected = None

    def intent_path(self, _target, candidate, size):
        self.selected = (candidate.variant.id, size)
        return self.root / "intent.json"

    def prepare(self, _path):
        self.prepare_calls += 1
        return self.prepare_result

    def submit(self, _path):
        self.submit_calls += 1
        return self.submit_result

    def reconcile(self, _path):
        self.reconcile_calls += 1
        return self.reconcile_result


class AutoPurchaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = StateStore(self.root / "state.db")
        self.events = []

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def coordinator(self, runner):
        return AutoPurchaseCoordinator(
            self.state,
            (target(),),
            self.root,
            notifier=self.events.append,
            runner_factory=lambda _policy: runner,
        )

    def test_three_pair_policy_uses_preferred_available_size_once(self):
        runner = FakeRunner(self.root)
        coordinator = self.coordinator(runner)
        coordinator(observation())
        coordinator(observation())
        self.assertEqual(runner.selected, ("v9", "9"))
        self.assertEqual(runner.prepare_calls, 1)
        self.assertEqual(runner.submit_calls, 1)
        self.assertEqual(self.events[0]["quantity"], 3)
        self.assertEqual(self.events[0]["status"], "order_confirmed")

    def test_manual_prepare_resumes_but_notifies_only_on_state_change(self):
        runner = FakeRunner(
            self.root,
            prepare=PurchaseRunResult("manual_auth_required", "prepare"),
        )
        coordinator = self.coordinator(runner)
        coordinator(observation())
        coordinator(observation())
        self.assertEqual(runner.prepare_calls, 2)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0]["status"], "manual_auth_required")

    def test_unknown_submission_is_reconcile_only(self):
        runner = FakeRunner(
            self.root,
            submit=PurchaseRunResult("result_unknown", "reconcile"),
        )
        coordinator = self.coordinator(runner)
        coordinator(observation())
        coordinator(observation())
        self.assertEqual(runner.submit_calls, 1)
        self.assertEqual(runner.reconcile_calls, 1)

    def test_runner_exception_becomes_one_failure_event(self):
        class Broken(FakeRunner):
            def prepare(self, _path):
                raise TimeoutError

        runner = Broken(self.root)
        coordinator = self.coordinator(runner)
        coordinator(observation())
        coordinator(observation())
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0]["status"], "failed")


class OpenClawNotificationTests(unittest.TestCase):
    def test_notification_is_sanitized_and_uses_argv(self):
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")

        OpenClawMessageSink("feishu", "user-id", runner=runner)({
            "status": "order_confirmed",
            "product": "Shoe <private>",
            "size": "9",
            "quantity": 3,
            "order_reference": "#123",
        })
        args, kwargs = calls[0]
        self.assertEqual(args[:4], ["openclaw", "message", "send", "--json"])
        self.assertNotIn("<", args[-1])
        self.assertTrue(kwargs["check"] is False)


class DropbotRunnerTests(unittest.TestCase):
    def test_private_exact_intent_enforces_three_pair_all_in_ceiling(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            executable = root / "dropbot"
            secret = root / "secret.env"
            executable.write_text("#!/bin/sh\n")
            executable.chmod(0o700)
            secret.write_text("protected=true\n")
            secret.chmod(0o600)
            configured = StandingPurchasePolicy(
                sizes=policy().sizes,
                quantity_per_product=3,
                max_all_in_per_unit_cents=30000,
                currency="USD",
                runner_path=str(executable),
                secret_file=str(secret),
            )
            calls = []

            def process(args, **kwargs):
                calls.append((args, kwargs))
                return subprocess.CompletedProcess(
                    args, 0, stdout='{"event":"checkout_ready"}\n', stderr=""
                )

            runner = DropbotCommandRunner(configured, root / "intents", runner=process)
            item = observation().candidates[1]
            path = runner.intent_path(target(), item, "9")
            document = json.loads(path.read_text())
            self.assertEqual(document["quantity"], 3)
            self.assertEqual(document["max_total_cents"], 90000)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(runner.prepare(path).status, "checkout_ready")
            self.assertEqual(calls[0][0][0], str(executable))
            self.assertIn("DROPBOT_STATE_DIR", calls[0][1]["env"])


if __name__ == "__main__":
    unittest.main()
