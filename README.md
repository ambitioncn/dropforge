# DropForge

DropForge is an open-source, multi-store drop monitor. It watches independent
targets concurrently, applies explicit product/variant/price rules, records
state transitions in SQLite, and emits public JSON events that a notification
or checkout worker can consume.

The first adapter supports public Shopify discovery endpoints. The core is
provider-neutral: add an adapter without changing the scheduler or state store.
Stores that deny public JSON discovery are reported as `blocked`; they can be
handled by the planned same-session browser adapter without pretending the site
is out of stock.

## Features

- **Multiple drops:** each target has its own store, rule and poll interval;
  checks run concurrently with a configurable global limit.
- **No noisy alerts:** SQLite stores observation digests and emits only changes.
- **Safe matching:** exact titles/sizes and price ceilings; `Any` is rejected.
- **Auditable:** the MVP uses only the Python standard library.
- **Human verification:** CAPTCHA, queue, login, 3DS and payment live outside the
  monitor. A browser executor should pause and hand the same session to a human.

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

JSON is printed only when a target changes between `available`, `unavailable`,
`blocked`, and `error`. Query recent transitions with:

```bash
dropforge --config drops.toml events --limit 20
```

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

## Architecture

```text
drops.toml -> MonitorEngine -> Adapter registry -> public store endpoints
                    |
                    +-> matching + price policy
                    +-> SQLite transition state
                    +-> JSON event sink -> notifier / guarded browser executor
```

Adapters are read-only discovery components implementing
`discover(target) -> list[Product]`. Checkout automation is deliberately a
separate process boundary. It must re-check the exact cart and final total,
require explicit authorization, use an idempotency ledger, and stop for
CAPTCHA/3DS.

## Security and responsible use

- Respect store terms, rate limits, purchase limits, and local law.
- Do not bypass CAPTCHA, queues, access controls, or anti-bot measures.
- Never commit cookies, browser profiles, addresses, or payment data.
- Do not put card data in command arguments, logs, TOML, or chat.
- Checkout plugins must default to preparation-only; payment needs fresh,
  explicit authorization and a verified total ceiling.

## Roadmap

- Guarded OpenClaw/Playwright browser executor with same-session human handoff.
- Notification sinks using protected secret references.
- Generic JSON, WooCommerce, and retailer-specific public API adapters.
- Long-running service packaging, metrics, dashboard, and container image.

## Development

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for adapter rules.
