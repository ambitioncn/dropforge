# Contributing

## Adapter contract

Adapters perform read-only discovery and return normalized `Product` objects.
They must use documented/public endpoints, bounded timeouts, and honest error
states. They must not evade bot defenses, solve CAPTCHAs, mutate carts, log in,
or submit orders.

## Checkout plugins

Checkout belongs in a separate optional package or process. A proposal must
include exact target matching, unit/final price ceilings, a private idempotency
ledger, explicit purchase authorization, redacted logs, and a human handoff for
CAPTCHA/3DS/login. Tests must never place a live order.

## Pull requests

Run the README test suite. Never include real credentials, cookies, browser
profiles, shipping details, or payment data in fixtures.
