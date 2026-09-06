# Architecture

## Data flow

1. The TOML loader validates every target before network work begins.
2. `MonitorEngine` schedules enabled targets independently and bounds global
   concurrency with a semaphore.
3. An adapter reads public storefront data and normalizes it into products and
   variants. The explicit `shopify_browser` adapter falls back after access is
   blocked, using a temporary OpenClaw browser tab and same-origin read-only
   request. Challenge/password pages remain blocked.
4. Match rules select explicit titles, variants, and price ceilings.
5. `StateStore` records the latest observation and appends an event only when
   the observation digest changes.
6. Event sinks print public JSON and optionally notify a Feishu custom bot using
   a protected environment reference. Delivery is bounded and failures are
   sanitized without stopping monitoring.
7. SQLite persists operator start/stop overrides. The CLI and loopback-only
   dashboard share that control plane; every watch loop rechecks it.

## Extension points

### Store adapters

Implement `Adapter.discover(target) -> list[Product]`. Adapters are read-only,
must use bounded timeouts, and should convert rate limits or access controls to
`AdapterBlocked` instead of trying to evade them.

### Event sinks

`on_change` receives an `Observation`. Sinks must treat product fields as
untrusted text and keep credentials in a protected secret store.

`FeishuWebhookSink` accepts an environment variable name, never a webhook in
configuration. It permits only official Feishu/Lark custom-bot endpoints, sends
a minimal transition summary off the asyncio event loop, bounds both timeout
and response size, and does not expose webhook URLs or response bodies.

### Operations controls

`TargetController` validates target IDs against loaded configuration before
writing an override. `MonitorEngine` reads those overrides for one-shot and
continuous operation. `DashboardServer` binds only to literal IPv4 or IPv6
loopback, sends restrictive browser headers, and requires JSON plus a custom
header on control requests to force a browser CORS preflight for cross-origin
attempts.

### Checkout workers

Checkout is intentionally out of process. `GuardedCheckoutExecutor` consumes an
immutable candidate-bound purchase intent, rebuilds and verifies a one-line
cart, enforces unit/cart/final-total ceilings, acquires a per-intent lock, and
records every transition in a mode-600 ledger. `PurchaseAuthorization` is
short-lived, one-use, and bound to the intent plus verified total.

`OpenClawCheckoutDriver` retains a deterministic named tab. CAPTCHA/login/3DS
states return `manual_auth_required` without bypass so the operator can take
over that same tab. Once submission begins, exceptions and ambiguous pages
become `result_unknown`; only reconciliation can leave that state.

## Scaling

One process and SQLite are sufficient for dozens of targets. For distributed
deployment, replace `StateStore` with a transactional database and use a queue
whose delivery key is the candidate key. Do not scale polling by reducing
intervals below a retailer's published limits.
