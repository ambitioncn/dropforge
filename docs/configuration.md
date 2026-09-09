# Configuration

DropForge reads one TOML file. `dropforge --config PATH validate` performs all
configuration checks without polling, sending notifications, or opening a
browser.

## Service settings

| Key | Default | Constraint |
| --- | --- | --- |
| `state_path` | `.dropforge/state.db` | Parent must be private and writable. |
| `max_concurrency` | `8` | Integer from 1 through 64. |
| `request_timeout_seconds` | `12` | Positive seconds. |
| `browser_profile` | unset | Name only; never a profile path or exported profile. |
| `browser_timeout_seconds` | `30` | Positive seconds. |
| `error_confirmations` | `3` | Consecutive transient errors required before changing stable state; 1 through 10. |
| `feishu_webhook_env` | unset | Uppercase environment-variable name, never a URL. |
| `feishu_timeout_seconds` | `10` | Positive seconds. |
| `dashboard_host` | `127.0.0.1` | Exactly `127.0.0.1` or `::1`. |
| `dashboard_port` | `8765` | Integer from 1 through 65535. |

Relative state paths are resolved from the configuration file directory. For a
container, use `/data/state.db`; for the systemd unit, use the absolute path
under the operator's `.local/state/dropforge` directory.

## Targets and matching

Every target needs a unique `id`, HTTPS `store`, non-empty query, and polling
interval of at least five seconds. `adapter = "shopify"` uses only public
endpoints. `adapter = "shopify_browser"` permits the bounded read-only browser
fallback only for HTTP 401, 403, or 429 blocking. `adapter = "sfcc_category"`
reads public Salesforce Commerce Cloud category product tiles and treats
401/403/429 responses as blocked.

Prefer `match.title` for exact title matching. `match.sizes` and
`match.max_unit_price_cents` further narrow candidates. `Any`, blank filters,
non-positive prices, and ambiguous variants are rejected. Configuration price
values are integer minor currency units, not floating-point amounts.

For catalog-change monitoring only, `match.all_products = true` explicitly
matches every available product in the category. It cannot be combined with
title filters and does not weaken the guarded checkout executor's exact intent
requirements.

Runtime `start` and `stop` overrides are stored in SQLite and take precedence
over the configured `enabled` default until changed again.

Transient adapter failures use `error_confirmations` to avoid one-sample event
flapping. Successful, unavailable, blocked, and candidate-set changes remain
immediate. A target with no previous baseline records its first error so a new
misconfiguration is still visible.

## Secret references

If notifications are enabled, put the Feishu/Lark webhook only in a protected
runtime environment entry and set `feishu_webhook_env` to its name. For the
systemd unit, the optional `%h/.config/dropforge/environment` must be mode 0600.
Do not commit that file. Prefer the host's secret manager where available.

No configuration field accepts credentials, shipping addresses, card data,
cookies, browser profiles, or checkout URLs.
