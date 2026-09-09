# DropForge

DropForge is an open-source, multi-store drop monitor. It watches independent
targets concurrently, applies explicit product/variant/price rules, records
state transitions in SQLite, and emits public JSON events that a notification
or checkout worker can consume.

The Shopify adapter supports public discovery endpoints. Targets configured as
`shopify_browser` first use those endpoints, then fall back on a temporary tab
in an OpenClaw managed browser only when access is blocked. CAPTCHA, queue and
password pages remain `blocked`; the adapter never clicks, logs in or mutates a
cart.

The `sfcc_category` adapter monitors public Salesforce Commerce Cloud category
pages, including explicit catalog-wide change detection with
`match.all_products = true`. Access limits remain `blocked`, and this read-only
mode never performs cart or checkout actions.

## Features

- **Multiple drops:** each target has its own store, rule and poll interval;
  checks run concurrently with a configurable global limit.
- **No noisy alerts:** SQLite stores observation digests and emits only changes.
- **Safe matching:** exact titles/sizes and price ceilings; `Any` is rejected.
- **Auditable:** the MVP uses only the Python standard library.
- **Guarded checkout:** exact cart and final totals, a private idempotency
  ledger, expiring one-use authorization, and unknown-result reconciliation.
- **Human verification:** CAPTCHA, login and 3DS pause the executor and preserve
  the same named OpenClaw browser tab for takeover.
- **Standing purchase policies:** an optional operator-local policy can bridge
  a monitored target to a guarded external checkout runner with product-level
  idempotency, exact sizes/quantity/currency, and an all-in total ceiling.
- **Operations:** transition-only Feishu notifications, persistent task
  start/stop controls, and a dashboard that can bind only to loopback.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
cp examples/drops.toml drops.toml
dropforge --config drops.toml validate
dropforge --config drops.toml once
dropforge --config drops.toml watch
```

Python 3.11 through 3.14 are supported. See the focused guides for
[installation](docs/installation.md), [configuration](docs/configuration.md),
[operations and deployment](docs/operations.md), [security](docs/security.md),
[browser handoff](docs/browser-handoff.md), and [releases](docs/release.md).

JSON is printed only when a target changes between `available`, `unavailable`,
`blocked`, and `error`. Query recent transitions with:

```bash
dropforge --config drops.toml events --limit 20
```

## Operations and Feishu

Runtime target controls are persisted in the same SQLite database. They affect
`once`, `watch`, and the dashboard and survive process restarts:

```bash
dropforge --config drops.toml status
dropforge --config drops.toml stop unique-target
dropforge --config drops.toml start unique-target
dropforge --config drops.toml dashboard
```

The dashboard monitors targets while serving status and start/stop controls at
`http://127.0.0.1:8765`. Both configuration and runtime validation reject
non-loopback bind addresses. It is a local operator surface, not an
Internet-facing service; use an authenticated local tunnel rather than changing
the bind address.

To notify a Feishu custom bot on deduplicated state transitions, configure only
the name of a protected environment entry:

```toml
[service]
feishu_webhook_env = "DROP_FORGE_FEISHU_WEBHOOK"
feishu_timeout_seconds = 10
```

The referenced value must be an HTTPS custom-bot URL on `open.feishu.cn` or
`open.larksuite.com`. Never put that URL in TOML, source, command arguments, or
logs. If delivery fails, monitoring continues and emits a sanitized local error;
the webhook URL and response body are never printed. No Feishu message is sent
by `validate`, `status`, `events`, `start`, `stop`, or `once`.

## Configuration

```toml
[[drops]]
id = "unique-target"
adapter = "shopify"
store = "https://shop.example.com"
query = "product search hint"
interval_seconds = 15

[drops.match]
title = "EXACT PRODUCT TITLE"
sizes = ["L"]
max_unit_price_cents = 15000
```

Use `title_contains` only when a site's title is unstable. Poll intervals under
five seconds are rejected to avoid abusive traffic.

Use `title_any_contains` for an explicit OR-list such as several possible shoe
model names. Numeric sizes are compared exactly: `9` never matches `9.5`.

### Optional standing purchase policy

Standing purchases are disabled unless a target has a complete
`[drops.purchase]` block. Paths point to an operator-owned guarded runner and a
mode-0600 local secret file; secret values never enter TOML, SQLite, argv, or
notifications.

```toml
[service]
openclaw_notify_channel = "feishu"
openclaw_notify_target = "operator-reference"

[drops.match]
title_any_contains = ["jordan", "sneaker"]
sizes = ["8", "8.5", "9", "9.5", "10", "10.5"]
max_unit_price_cents = 30000

[drops.purchase]
sizes = ["9", "9.5", "10", "8.5", "8", "10.5"]
quantity_per_product = 3
max_all_in_per_unit_cents = 30000
currency = "USD"
runner_path = "/absolute/path/to/guarded-runner"
secret_file = "/absolute/path/to/mode-0600-secret-file"
```

The first available size in policy order is used. DropForge requests the
configured quantity in one exact cart and never splits orders to evade retailer
limits. The final checkout total must be no more than
`quantity_per_product * max_all_in_per_unit_cents`. One product ID receives one
durable claim across variants and restarts. CAPTCHA/3DS pauses for human
takeover; an unknown submit result is reconcile-only.

For a store whose public JSON endpoints return 401, 403 or 429, select the
explicit browser fallback and (optionally) a managed OpenClaw profile:

```toml
[service]
browser_profile = "openclaw"
browser_timeout_seconds = 30

[[drops]]
adapter = "shopify_browser"
```

The fallback opens the storefront read-only, requests `/products.json` inside
that same browser origin, and closes only the tab it created. It reports
CAPTCHA, queue, storefront-password and rate-limit responses honestly. It does
not solve or bypass them. `browser.evaluateEnabled` must be enabled in OpenClaw.

## Architecture

```text
drops.toml -> MonitorEngine -> Adapter registry -> public endpoint
                    |                              -> OpenClaw browser fallback
                    |
                    +-> matching + price policy
                    +-> SQLite transition state
                    +-> JSON + Feishu sinks -> operator / guarded checkout
                    +-> SQLite controls <- CLI / loopback dashboard
```

Adapters are read-only discovery components implementing
`discover(target) -> list[Product]`. Checkout automation is deliberately a
separate process boundary. It must re-check the exact cart and final total,
require explicit authorization, use an idempotency ledger, and stop for
CAPTCHA/3DS.

## Guarded checkout API

`GuardedCheckoutExecutor` consumes an immutable `PurchaseIntent`, a private
`CheckoutLedger`, and a checkout driver. `OpenClawCheckoutDriver` prepares an
exact cart in a named managed-browser tab, navigates to checkout, and leaves the
tab open for human entry or challenge completion. DropForge never accepts or
stores shipping or payment secrets.

Calling `prepare()` deliberately clears and replaces the cart in that named
browser session; it never submits an order. Operators should use a dedicated
OpenClaw profile when they need to preserve an unrelated shopping cart.

Submission requires a short-lived `PurchaseAuthorization` bound to the exact
intent, explicit ISO currency, and verified final total. The authorization is
consumed before the pay control is clicked. A timeout or ambiguous page becomes
`result_unknown`; that state can only be reconciled and cannot be submitted
again.

State flow:

```text
created -> cart_verified -> checkout_ready -> submitting -> order_confirmed
              |                  |               |-------> payment_failed
              +------------------+---------------> manual_auth_required
                                                  +-------> result_unknown
                                                            |
                                                            +-> reconcile only
```

## Security and responsible use

- Respect store terms, rate limits, purchase limits, and local law.
- Do not bypass CAPTCHA, queues, access controls, or anti-bot measures.
- Never commit cookies, browser profiles, addresses, or payment data.
- Do not put card data in command arguments, logs, TOML, or chat.
- Checkout plugins must default to preparation-only; payment needs fresh,
  explicit authorization and a verified total ceiling.

## Roadmap

- Generic JSON, WooCommerce, and additional retailer-specific public API adapters.
- Metrics and additional notification sinks.

## Development

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests
ruff check src tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for adapter rules.

Before publishing, work through the
[public-release readiness review](docs/public-release-readiness.md). Preparing
an artifact is not permission to push, publish, or deploy it.
