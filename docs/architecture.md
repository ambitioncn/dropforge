# Architecture

## Data flow

1. The TOML loader validates every target before network work begins.
2. `MonitorEngine` schedules enabled targets independently and bounds global
   concurrency with a semaphore.
3. An adapter reads public storefront data and normalizes it into products and
   variants.
4. Match rules select explicit titles, variants, and price ceilings.
5. `StateStore` records the latest observation and appends an event only when
   the observation digest changes.
6. Event sinks notify operators or hand a candidate to a separately governed
   checkout worker.

## Extension points

### Store adapters

Implement `Adapter.discover(target) -> list[Product]`. Adapters are read-only,
must use bounded timeouts, and should convert rate limits or access controls to
`AdapterBlocked` instead of trying to evade them.

### Event sinks

`on_change` receives an `Observation`. Sinks must treat product fields as
untrusted text and keep credentials in a protected secret store.

### Checkout workers

Checkout is intentionally out of process. The worker must consume an immutable
candidate key and public purchase policy, rebuild and verify the exact cart,
enforce the final total, acquire a per-intent lock, and record a terminal result
before retrying. Manual challenges pause the worker and preserve the same
browser session for operator takeover.

## Scaling

One process and SQLite are sufficient for dozens of targets. For distributed
deployment, replace `StateStore` with a transactional database and use a queue
whose delivery key is the candidate key. Do not scale polling by reducing
intervals below a retailer's published limits.
