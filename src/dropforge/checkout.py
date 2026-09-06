from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from .models import Candidate, normalize


TERMINAL_STATES = {"order_confirmed", "payment_failed", "cancelled"}
ALLOWED_TRANSITIONS = {
    None: {"created"},
    "created": {"cart_verified", "manual_auth_required", "cancelled"},
    "cart_verified": {"checkout_ready", "manual_auth_required", "cancelled"},
    "checkout_ready": {"submitting", "manual_auth_required", "cancelled"},
    "manual_auth_required": {
        "cart_verified", "checkout_ready", "submitting", "order_confirmed", "payment_failed",
        "result_unknown", "cancelled",
    },
    "submitting": {"order_confirmed", "payment_failed", "manual_auth_required", "result_unknown"},
    "result_unknown": {"order_confirmed", "payment_failed", "manual_auth_required", "cancelled"},
}
PUBLIC_LEDGER_FIELDS = {
    "session_ref", "challenge", "stage", "final_total_cents",
    "used_authorization_ids", "order_reference",
}


class CheckoutPolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class PurchaseIntent:
    store: str
    product_title: str
    variant_id: str
    variant_title: str
    quantity: int
    max_unit_price_cents: int
    max_total_cents: int
    candidate_key: str
    currency: str

    @classmethod
    def from_candidate(
        cls,
        candidate: Candidate,
        *,
        quantity: int,
        max_total_cents: int,
        currency: str,
    ) -> "PurchaseIntent":
        intent = cls(
            store=candidate.store,
            product_title=candidate.product.title,
            variant_id=candidate.variant.id,
            variant_title=candidate.variant.title,
            quantity=quantity,
            max_unit_price_cents=candidate.variant.price_cents,
            max_total_cents=max_total_cents,
            candidate_key=candidate.key,
            currency=currency.upper(),
        )
        intent.validate()
        return intent

    def validate(self) -> None:
        parsed = urlparse(self.store)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("store must be an https URL")
        if normalize(self.product_title) in {"", "any", "all"}:
            raise ValueError("an exact product title is required")
        if normalize(self.variant_title) in {"", "any", "all"} or not self.variant_id:
            raise ValueError("an exact variant is required")
        if self.quantity < 1 or self.quantity > 10:
            raise ValueError("quantity must be between 1 and 10")
        if self.max_unit_price_cents <= 0 or self.max_total_cents <= 0:
            raise ValueError("price ceilings must be positive")
        if self.max_unit_price_cents * self.quantity > self.max_total_cents:
            raise ValueError("item subtotal exceeds total ceiling")
        if not self.candidate_key:
            raise ValueError("candidate_key is required")
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("currency must be an explicit ISO 4217 code")

    def public_dict(self) -> dict[str, Any]:
        return {
            "store": self.store,
            "product_title": self.product_title,
            "variant_id": self.variant_id,
            "variant_title": self.variant_title,
            "quantity": self.quantity,
            "max_unit_price_cents": self.max_unit_price_cents,
            "max_total_cents": self.max_total_cents,
            "candidate_key": self.candidate_key,
            "currency": self.currency,
        }

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(self.public_dict(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()[:24]


@dataclass(frozen=True)
class CartLine:
    product_title: str
    variant_id: str
    variant_title: str
    quantity: int
    unit_price_cents: int


@dataclass(frozen=True)
class CartSnapshot:
    lines: tuple[CartLine, ...]
    total_cents: int
    currency: str


@dataclass(frozen=True)
class CheckoutSnapshot:
    cart: CartSnapshot
    final_total_cents: int | None
    challenge: str | None = None


@dataclass(frozen=True)
class CheckoutOutcome:
    status: str
    order_reference: str | None = None
    challenge: str | None = None


@dataclass(frozen=True)
class PurchaseAuthorization:
    authorization_id: str
    intent_fingerprint: str
    max_total_cents: int
    expires_at: float

    @classmethod
    def issue(
        cls,
        intent: PurchaseIntent,
        *,
        confirmed_total_cents: int,
        ttl_seconds: float = 300,
        confirmed: bool = False,
    ) -> "PurchaseAuthorization":
        intent.validate()
        if not confirmed:
            raise CheckoutPolicyError("explicit purchase confirmation is required")
        if ttl_seconds <= 0 or ttl_seconds > 900:
            raise CheckoutPolicyError("authorization TTL must be between 1 and 900 seconds")
        if confirmed_total_cents > intent.max_total_cents:
            raise CheckoutPolicyError("confirmed total exceeds intent ceiling")
        return cls(
            authorization_id=uuid.uuid4().hex,
            intent_fingerprint=intent.fingerprint,
            max_total_cents=confirmed_total_cents,
            expires_at=time.time() + ttl_seconds,
        )

    def validate(self, intent: PurchaseIntent, final_total_cents: int, used_ids: set[str]) -> None:
        if self.intent_fingerprint != intent.fingerprint:
            raise CheckoutPolicyError("authorization does not match purchase intent")
        if self.authorization_id in used_ids:
            raise CheckoutPolicyError("authorization has already been consumed")
        if time.time() > self.expires_at:
            raise CheckoutPolicyError("authorization has expired")
        if final_total_cents > self.max_total_cents or final_total_cents > intent.max_total_cents:
            raise CheckoutPolicyError("final total exceeds authorization ceiling")


class CheckoutDriver(Protocol):
    @property
    def session_ref(self) -> str: ...
    def prepare(self, intent: PurchaseIntent) -> CheckoutSnapshot: ...
    def inspect(self, intent: PurchaseIntent) -> CheckoutSnapshot: ...
    def submit(self, intent: PurchaseIntent) -> CheckoutOutcome: ...
    def reconcile(self, intent: PurchaseIntent) -> CheckoutOutcome: ...


class CheckoutLedger:
    def __init__(self, directory: Path, intent: PurchaseIntent):
        self.directory = directory
        self.intent = intent
        self.path = directory / f"{intent.fingerprint}.json"
        self.lock_path = directory / f"{intent.fingerprint}.lock"

    def read(self) -> dict[str, Any] | None:
        return json.loads(self.path.read_text()) if self.path.exists() else None

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("w") as handle:
            os.chmod(self.lock_path, 0o600)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CheckoutPolicyError("purchase intent is already running") from exc
            yield

    def transition(self, state: str, **fields: Any) -> dict[str, Any]:
        unknown = set(fields) - PUBLIC_LEDGER_FIELDS
        if unknown:
            raise CheckoutPolicyError(f"non-public ledger fields are forbidden: {', '.join(sorted(unknown))}")
        current = self.read()
        current_state = current.get("state") if current else None
        if current_state in TERMINAL_STATES:
            raise CheckoutPolicyError(f"purchase intent is terminal: {current_state}")
        if state not in ALLOWED_TRANSITIONS.get(current_state, set()):
            raise CheckoutPolicyError(f"invalid checkout transition: {current_state!r} -> {state!r}")
        history = list(current.get("history", [])) if current else []
        carried = {
            key: value for key, value in (current or {}).items()
            if key not in {"schema", "fingerprint", "intent", "state", "history"}
        }
        record = {
            "schema": 1,
            "fingerprint": self.intent.fingerprint,
            "intent": self.intent.public_dict(),
            "state": state,
            "history": [*history, state],
            "used_authorization_ids": list(current.get("used_authorization_ids", [])) if current else [],
            **carried,
            **fields,
        }
        self._write(record)
        return record

    def _write(self, record: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".checkout-", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as handle:
                json.dump(record, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temp_name, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def validate_checkout(snapshot: CheckoutSnapshot, intent: PurchaseIntent) -> int:
    if len(snapshot.cart.lines) != 1:
        raise CheckoutPolicyError("cart must contain exactly one line")
    line = snapshot.cart.lines[0]
    if normalize(line.product_title) != normalize(intent.product_title):
        raise CheckoutPolicyError("cart product does not exactly match intent")
    if line.variant_id != intent.variant_id or normalize(line.variant_title) != normalize(intent.variant_title):
        raise CheckoutPolicyError("cart variant does not exactly match intent")
    if line.quantity != intent.quantity:
        raise CheckoutPolicyError("cart quantity does not match intent")
    if line.unit_price_cents > intent.max_unit_price_cents:
        raise CheckoutPolicyError("cart unit price exceeds intent ceiling")
    if snapshot.cart.total_cents > intent.max_total_cents:
        raise CheckoutPolicyError("cart total exceeds intent ceiling")
    if snapshot.cart.currency.upper() != intent.currency:
        raise CheckoutPolicyError("cart currency does not match intent")
    if snapshot.final_total_cents is None:
        raise CheckoutPolicyError("verified final checkout total is required")
    if snapshot.final_total_cents > intent.max_total_cents:
        raise CheckoutPolicyError("final checkout total exceeds intent ceiling")
    return snapshot.final_total_cents


class GuardedCheckoutExecutor:
    def __init__(self, *, driver: CheckoutDriver, ledger: CheckoutLedger):
        self.driver = driver
        self.ledger = ledger
        self.intent = ledger.intent
        self.intent.validate()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", self.driver.session_ref):
            raise CheckoutPolicyError("driver session_ref must be an opaque non-URL identifier")

    def prepare(self) -> dict[str, Any]:
        with self.ledger.locked():
            current = self.ledger.read()
            resuming = bool(
                current
                and current.get("state") == "manual_auth_required"
                and current.get("stage") == "prepare"
            )
            if current is not None and not resuming:
                raise CheckoutPolicyError("checkout preparation is not repeatable; inspect existing state")
            if not resuming:
                self.ledger.transition("created", session_ref=self.driver.session_ref)
            snapshot = self.driver.prepare(self.intent)
            if snapshot.challenge:
                if resuming:
                    return current
                return self.ledger.transition(
                    "manual_auth_required", session_ref=self.driver.session_ref,
                    challenge=snapshot.challenge, stage="prepare",
                )
            total = validate_checkout(snapshot, self.intent)
            self.ledger.transition("cart_verified", session_ref=self.driver.session_ref)
            return self.ledger.transition(
                "checkout_ready", session_ref=self.driver.session_ref, final_total_cents=total,
            )

    def submit(self, authorization: PurchaseAuthorization) -> dict[str, Any]:
        with self.ledger.locked():
            current = self.ledger.read()
            if not current or current.get("state") not in {"checkout_ready", "manual_auth_required"}:
                raise CheckoutPolicyError("checkout is not ready for submission")
            if current.get("state") == "manual_auth_required" and current.get("stage") != "submit":
                raise CheckoutPolicyError("manual post-submit state requires reconciliation")
            snapshot = self.driver.inspect(self.intent)
            if snapshot.challenge:
                if current["state"] == "manual_auth_required":
                    return current
                return self.ledger.transition(
                    "manual_auth_required", session_ref=self.driver.session_ref,
                    challenge=snapshot.challenge, stage="submit",
                )
            total = validate_checkout(snapshot, self.intent)
            used = set(current.get("used_authorization_ids", []))
            authorization.validate(self.intent, total, used)
            used.add(authorization.authorization_id)
            self.ledger.transition(
                "submitting", session_ref=self.driver.session_ref,
                final_total_cents=total, used_authorization_ids=sorted(used),
            )
            try:
                outcome = self.driver.submit(self.intent)
            except Exception:
                return self.ledger.transition(
                    "result_unknown", session_ref=self.driver.session_ref,
                    final_total_cents=total, used_authorization_ids=sorted(used),
                )
            return self._record_outcome(outcome, used)

    def reconcile(self) -> dict[str, Any]:
        with self.ledger.locked():
            current = self.ledger.read()
            if not current or current.get("state") not in {
                "submitting", "result_unknown", "manual_auth_required"
            }:
                raise CheckoutPolicyError("checkout does not require reconciliation")
            if current.get("state") == "manual_auth_required" and current.get("stage") != "post_submit":
                raise CheckoutPolicyError("manual pre-submit state is not reconcilable")
            outcome = self.driver.reconcile(self.intent)
            if outcome.status == "result_unknown" and current["state"] == "result_unknown":
                return current
            if outcome.status == "manual_auth_required" and current["state"] == "manual_auth_required":
                return current
            return self._record_outcome(outcome, set(current.get("used_authorization_ids", [])))

    def _record_outcome(self, outcome: CheckoutOutcome, used: set[str]) -> dict[str, Any]:
        allowed = {"order_confirmed", "payment_failed", "manual_auth_required", "result_unknown"}
        if outcome.status not in allowed:
            raise CheckoutPolicyError("checkout driver returned an invalid outcome")
        fields: dict[str, Any] = {
            "session_ref": self.driver.session_ref,
            "used_authorization_ids": sorted(used),
        }
        if outcome.order_reference:
            if not re.fullmatch(r"[A-Za-z0-9._#-]{1,128}", outcome.order_reference):
                raise CheckoutPolicyError("order reference is not safe for the public ledger")
            fields["order_reference"] = outcome.order_reference
        if outcome.challenge:
            fields["challenge"] = outcome.challenge
            fields["stage"] = "post_submit"
        return self.ledger.transition(outcome.status, **fields)
