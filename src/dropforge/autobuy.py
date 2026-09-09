from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import Candidate, DropTarget, Observation, StandingPurchasePolicy, variant_matches_size
from .notifications import NotificationError
from .state import StateStore


TERMINAL = {"order_confirmed", "payment_failed", "failed"}


@dataclass(frozen=True)
class PurchaseRunResult:
    status: str
    phase: str | None = None
    order_reference: str | None = None


def _payload(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("event"), str):
            return value
    return {}


class DropbotCommandRunner:
    """Bridge to a local guarded headed-browser checkout runner.

    The runner receives only an exact public intent path. Shipping and payment
    values remain in its mode-0600 secret file and are never read by DropForge.
    """

    def __init__(
        self,
        policy: StandingPurchasePolicy,
        intent_dir: Path,
        *,
        runner: Callable = subprocess.run,
        timeout: float = 420,
    ):
        self.policy = policy
        self.intent_dir = intent_dir
        self.runner = runner
        self.timeout = timeout

    def _check_files(self) -> None:
        runner = Path(self.policy.runner_path)
        secret = Path(self.policy.secret_file)
        if not runner.is_file() or not os.access(runner, os.X_OK):
            raise RuntimeError("guarded purchase runner is unavailable")
        if not secret.is_file() or secret.stat().st_mode & 0o077:
            raise RuntimeError("purchase secret file is missing or not mode 0600")

    def intent_path(
        self, target: DropTarget, candidate: Candidate, size: str, quantity: int | None = None
    ) -> Path:
        quantity = self.policy.quantity_per_product if quantity is None else quantity
        key = hashlib.sha256(
            f"{target.id}\0{candidate.product.id}".encode()
        ).hexdigest()[:24]
        path = self.intent_dir / f"{key}.json"
        document = {
            "store": candidate.store,
            "title": candidate.product.title,
            "size": size,
            "quantity": quantity,
            "max_unit_price_cents": self.policy.max_all_in_per_unit_cents,
            "max_total_cents": (
                self.policy.max_all_in_per_unit_cents * quantity
            ),
            "handle": candidate.product.handle,
        }
        self.intent_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document, sort_keys=True) + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        return path

    def _run(self, command: str, intent_path: Path) -> tuple[int, dict]:
        args = [
            self.policy.runner_path,
            "--env-file", self.policy.secret_file,
            command,
            "--intent", str(intent_path),
        ]
        if command == "submit":
            args.append("--authorize-purchase")
        environment = os.environ.copy()
        environment["DROPBOT_STATE_DIR"] = str(self.intent_dir / "runner-state")
        completed = self.runner(
            args,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
            env=environment,
        )
        return completed.returncode, _payload(completed.stdout)

    def prepare(self, intent_path: Path) -> PurchaseRunResult:
        self._check_files()
        code, event = self._run("prepare", intent_path)
        if code == 0 and event.get("event") == "checkout_ready":
            return PurchaseRunResult("checkout_ready", "submit")
        if code == 4:
            return PurchaseRunResult("manual_auth_required", "prepare")
        return PurchaseRunResult("failed", "prepare")

    def submit(self, intent_path: Path) -> PurchaseRunResult:
        code, event = self._run("submit", intent_path)
        name = str(event.get("event", ""))
        reference = event.get("order_reference")
        if code == 0 and name == "order_confirmed":
            return PurchaseRunResult("order_confirmed", order_reference=reference)
        if code == 4 or name == "manual_auth_required":
            return PurchaseRunResult("manual_auth_required", "post_submit")
        if code == 5 or name == "payment_failed":
            return PurchaseRunResult("payment_failed")
        if code == 6 or name == "result_unknown":
            return PurchaseRunResult("result_unknown", "reconcile")
        return PurchaseRunResult("failed", "submit")

    def reconcile(self, intent_path: Path) -> PurchaseRunResult:
        code, event = self._run("reconcile", intent_path)
        name = str(event.get("state", event.get("event", "")))
        reference = event.get("order_reference")
        if code == 0 and name == "order_confirmed":
            return PurchaseRunResult("order_confirmed", order_reference=reference)
        if name == "payment_failed":
            return PurchaseRunResult("payment_failed")
        if name == "manual_auth_required":
            return PurchaseRunResult("manual_auth_required", "post_submit")
        return PurchaseRunResult("result_unknown", "reconcile")


def _size_for(candidate: Candidate, sizes: tuple[str, ...]) -> str | None:
    for size in sizes:
        if variant_matches_size(candidate.variant, size):
            return size
    return None


class AutoPurchaseCoordinator:
    def __init__(
        self,
        state: StateStore,
        targets: tuple[DropTarget, ...],
        intent_dir: Path,
        *,
        notifier: Callable[[dict], None] | None = None,
        runner_factory: Callable | None = None,
    ):
        self.state = state
        self.targets = {target.id: target for target in targets}
        self.intent_dir = intent_dir
        self.notifier = notifier
        self.runner_factory = runner_factory or (
            lambda policy: DropbotCommandRunner(policy, intent_dir)
        )

    def __call__(self, observation: Observation) -> None:
        target = self.targets.get(observation.target_id)
        if target is None or target.purchase is None or observation.status != "available":
            return
        policy = target.purchase
        grouped: dict[str, list[Candidate]] = {}
        for candidate in observation.candidates:
            grouped.setdefault(candidate.product.id, []).append(candidate)
        for product_id, candidates in grouped.items():
            self._run_product(target, policy, product_id, candidates)

    def _run_product(
        self,
        target: DropTarget,
        policy: StandingPurchasePolicy,
        product_id: str,
        candidates: list[Candidate],
    ) -> None:
        ranked = []
        for candidate in candidates:
            size = _size_for(candidate, policy.sizes)
            if size is not None and candidate.variant.price_cents <= policy.max_all_in_per_unit_cents:
                ranked.append((policy.sizes.index(size), candidate.key, candidate, size))
        if not ranked:
            return
        _rank, _key, candidate, size = min(ranked)
        claim = self.state.purchase_claim(target.id, product_id)
        if claim is None:
            if not self.state.reserve_purchase(target.id, product_id, candidate.key):
                return
            claim = self.state.purchase_claim(target.id, product_id)
        assert claim is not None
        if claim["status"] in TERMINAL:
            return
        prior_result = (
            claim["status"], claim.get("phase"), claim.get("payload")
        )
        try:
            runner = self.runner_factory(policy)
            status = claim["status"]
            phase = claim.get("phase")
            quantity = int(claim["payload"].get("quantity", policy.quantity_per_product))
            intent_path = runner.intent_path(target, candidate, size, quantity)
            if status == "result_unknown" or phase in {"submit", "post_submit", "reconcile"}:
                result = runner.reconcile(intent_path)
            else:
                self.state.update_purchase_claim(
                    target.id, product_id, status="in_progress", phase="prepare",
                    payload=claim["payload"],
                )
                result = runner.prepare(intent_path)
                if result.status == "failed" and policy.fallback_quantity is not None:
                    quantity = policy.fallback_quantity
                    intent_path = runner.intent_path(target, candidate, size, quantity)
                    result = runner.prepare(intent_path)
                if result.status == "checkout_ready":
                    self.state.update_purchase_claim(
                        target.id, product_id, status="in_progress", phase="submit",
                        payload=claim["payload"],
                    )
                    result = runner.submit(intent_path)
        except Exception:
            result = PurchaseRunResult("failed", "runner")
        event = {
            "status": result.status,
            "product": candidate.product.title,
            "size": size,
            "quantity": quantity,
            "order_reference": result.order_reference,
        }
        self.state.update_purchase_claim(
            target.id,
            product_id,
            status=result.status,
            phase=result.phase,
            payload=event,
        )
        meaningful_change = prior_result != (result.status, result.phase, event)
        if meaningful_change and self.notifier:
            try:
                self.notifier(event)
            except NotificationError:
                pass
