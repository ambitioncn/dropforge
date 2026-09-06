from __future__ import annotations

import json
from typing import Any

from .adapters.base import AdapterError
from .adapters.openclaw_browser import OpenClawBrowserClient
from .checkout import (
    CartLine,
    CartSnapshot,
    CheckoutOutcome,
    CheckoutSnapshot,
    PurchaseIntent,
)


INSPECT_SCRIPT = r"""
const challengeSelector = 'iframe[src*="captcha" i], iframe[src*="challenge" i], [data-sitekey], #challenge-form';
const text = document.body?.innerText?.toLowerCase() || '';
const challenge = document.querySelector(challengeSelector)
  ? 'captcha'
  : (text.includes('3d secure') || text.includes('verify your identity'))
    ? '3ds'
    : (text.includes('log in') && text.includes('checkout')) ? 'login' : null;
let cart;
try {
  const response = await fetch('/cart.js', {credentials: 'same-origin'});
  if (!response.ok) return {dropforgeCheckoutVersion: 1, status: 'error'};
  cart = await response.json();
} catch (_error) {
  return {dropforgeCheckoutVersion: 1, status: 'error'};
}
const due = document.querySelector('[data-checkout-payment-due-target], [data-checkout-payment-due]');
let finalTotal = null;
if (due) {
  const raw = due.getAttribute('data-checkout-payment-due-target')
    || due.getAttribute('data-checkout-payment-due') || '';
  if (/^\d+$/.test(raw)) finalTotal = Number(raw);
}
return {
  dropforgeCheckoutVersion: 1,
  status: 'ok',
  challenge,
  finalTotal,
  currency: String(cart.currency || ''),
  cartTotal: Number(cart.total_price),
  lines: (cart.items || []).map((item) => ({
    productTitle: String(item.product_title || ''),
    variantId: String(item.variant_id || ''),
    variantTitle: String(item.variant_title || ''),
    quantity: Number(item.quantity),
    unitPrice: Number(item.final_price ?? item.price)
  }))
};
""".strip()


OUTCOME_SCRIPT = r"""
const text = document.body?.innerText?.toLowerCase() || '';
const url = location.href.toLowerCase();
if (url.includes('thank_you') || url.includes('thank-you') || text.includes('order confirmed')) {
  const match = text.match(/order\s*#?\s*([a-z0-9-]+)/i);
  return {dropforgeCheckoutVersion: 1, status: 'order_confirmed', orderReference: match?.[1] || null};
}
if (text.includes('card was declined') || text.includes('payment could not be processed')) {
  return {dropforgeCheckoutVersion: 1, status: 'payment_failed'};
}
if (document.querySelector('iframe[src*="captcha" i], iframe[src*="challenge" i], [data-sitekey]')) {
  return {dropforgeCheckoutVersion: 1, status: 'manual_auth_required', challenge: 'captcha'};
}
if (text.includes('3d secure') || text.includes('verify your identity')) {
  return {dropforgeCheckoutVersion: 1, status: 'manual_auth_required', challenge: '3ds'};
}
return {dropforgeCheckoutVersion: 1, status: 'result_unknown'};
""".strip()


def _checkout_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and value.get("dropforgeCheckoutVersion") == 1:
        return value
    if isinstance(value, dict):
        for key in ("result", "value", "data"):
            if key in value:
                try:
                    return _checkout_payload(value[key])
                except AdapterError:
                    pass
    if isinstance(value, str):
        try:
            return _checkout_payload(json.loads(value))
        except json.JSONDecodeError:
            pass
    raise AdapterError("OpenClaw browser returned no checkout payload")


class OpenClawCheckoutDriver:
    """Guarded same-session cart/checkout transport; it never handles payment secrets."""

    def __init__(self, client: OpenClawBrowserClient, intent: PurchaseIntent):
        intent.validate()
        self.client = client
        self.intent = intent
        self.label = f"dropforge-checkout-{intent.fingerprint}"

    @property
    def session_ref(self) -> str:
        return self.label

    def _evaluate(self, script: str) -> dict[str, Any]:
        raw = self.client._run(
            "evaluate", "--target-id", self.label,
            "--timeout-ms", str(round(self.client.timeout * 1000)), "--fn", script,
        )
        return _checkout_payload(raw)

    def _ensure_session(self) -> None:
        try:
            self.client._run("focus", self.label)
        except AdapterError:
            self.client._run("open", self.intent.store, "--label", self.label)
        self.client._run("navigate", self.intent.store, "--target-id", self.label)

    def prepare(self, intent: PurchaseIntent) -> CheckoutSnapshot:
        self._ensure_session()
        payload = json.dumps({"variantId": intent.variant_id, "quantity": intent.quantity})
        script = f"""
const input = {payload};
const challenge = document.querySelector('iframe[src*="captcha" i], [data-sitekey], #challenge-form');
if (challenge) return {{dropforgeCheckoutVersion: 1, status: 'challenge', challenge: 'captcha'}};
const clear = await fetch('/cart/clear.js', {{method: 'POST', credentials: 'same-origin'}});
if (!clear.ok) return {{dropforgeCheckoutVersion: 1, status: 'error'}};
const add = await fetch('/cart/add.js', {{
  method: 'POST', credentials: 'same-origin', headers: {{'Content-Type': 'application/json'}},
  body: JSON.stringify({{items: [{{id: input.variantId, quantity: input.quantity}}]}})
}});
if (!add.ok) return {{dropforgeCheckoutVersion: 1, status: 'error'}};
return {{dropforgeCheckoutVersion: 1, status: 'prepared'}};
""".strip()
        result = self._evaluate(script)
        if result.get("status") == "challenge":
            return CheckoutSnapshot(CartSnapshot((), 0, ""), None, str(result["challenge"]))
        if result.get("status") != "prepared":
            raise AdapterError("browser could not prepare the exact cart")
        self.client._run("navigate", f"{intent.store}/checkout", "--target-id", self.label)
        return self.inspect(intent)

    def inspect(self, _intent: PurchaseIntent) -> CheckoutSnapshot:
        payload = self._evaluate(INSPECT_SCRIPT)
        if payload.get("status") != "ok":
            raise AdapterError("browser checkout inspection failed")
        lines = tuple(CartLine(
            product_title=str(item.get("productTitle", "")),
            variant_id=str(item.get("variantId", "")),
            variant_title=str(item.get("variantTitle", "")),
            quantity=int(item.get("quantity", 0)),
            unit_price_cents=int(item.get("unitPrice", 0)),
        ) for item in payload.get("lines", []))
        cart = CartSnapshot(
            lines=lines,
            total_cents=int(payload.get("cartTotal", 0)),
            currency=str(payload.get("currency", "")),
        )
        final_total = payload.get("finalTotal")
        return CheckoutSnapshot(
            cart=cart,
            final_total_cents=int(final_total) if final_total is not None else None,
            challenge=str(payload["challenge"]) if payload.get("challenge") else None,
        )

    def submit(self, _intent: PurchaseIntent) -> CheckoutOutcome:
        script = r"""
const buttons = [...document.querySelectorAll('button[type="submit"], input[type="submit"]')];
const button = buttons.find((item) => /pay now|complete order/i.test(item.innerText || item.value || ''));
if (!button || button.disabled) return {dropforgeCheckoutVersion: 1, status: 'error'};
button.click();
return {dropforgeCheckoutVersion: 1, status: 'clicked'};
""".strip()
        clicked = self._evaluate(script)
        if clicked.get("status") != "clicked":
            raise AdapterError("payment submit control is unavailable")
        self.client._run("wait", "--target-id", self.label, "--time", "2500")
        return self._outcome()

    def reconcile(self, _intent: PurchaseIntent) -> CheckoutOutcome:
        return self._outcome()

    def _outcome(self) -> CheckoutOutcome:
        payload = self._evaluate(OUTCOME_SCRIPT)
        return CheckoutOutcome(
            status=str(payload.get("status", "result_unknown")),
            order_reference=(str(payload["orderReference"]) if payload.get("orderReference") else None),
            challenge=(str(payload["challenge"]) if payload.get("challenge") else None),
        )
