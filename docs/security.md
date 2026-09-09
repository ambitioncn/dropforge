# Security model

## Guarantees

- Discovery is read-only and respects access-control, queue, rate-limit,
  storefront-password, and CAPTCHA states.
- Matching rejects ambiguity and enforces exact candidate, variant, quantity,
  currency, unit-price, cart-total, and final-total policy.
- Checkout submission needs a fresh, one-use authorization bound to the intent
  and verified total. The authorization is consumed before the click.
- A standing policy is itself explicit authorization only within its configured
  target, exact size set, quantity, currency, and all-in ceiling. Each discovered
  product is converted to an exact immutable intent before checkout.
- Product-level claims prevent a second variant or process restart from buying
  the same product again. Store limits are never evaded by splitting orders.
- Per-intent locking and a mode-0600 ledger prevent concurrent or duplicate
  submission. An unknown result can only be reconciled.
- CAPTCHA, login, and 3DS are same-session human handoffs; DropForge neither
  solves nor bypasses them.
- The dashboard accepts only literal IPv4 or IPv6 loopback addresses.

## Data classification

Repository and logs may contain public target identifiers, product titles,
prices, availability, sanitized states, opaque session references, and opaque
order references. They must not contain credentials, webhook URLs, shipping or
payment data, cookies, checkout URLs, exported browser profiles, private user
data, or challenge contents.

SQLite state, checkout ledgers, environment files, backups, and system journals
are operator-private. Restrict them to the service account and do not attach
them to bug reports. The service's default umask is 0077.

## Network and browser boundaries

Only configure HTTPS storefronts. Feishu delivery is restricted to official
HTTPS hosts and uses a bounded request/response. Dashboard traffic is local
operator traffic; loopback binding is defense in depth, not an authentication
system.

OpenClaw owns its browser session. Use a dedicated profile when cart mutation is
acceptable, because `prepare()` clears and replaces the named session's cart.
Do not share or export the profile. Human completion of a challenge happens in
that same visible tab and must be followed by inspection/reconciliation.

## Supply chain and disclosure

CI runs tests across supported Python versions, static/compile checks, package
builds, a container smoke test, and Gitleaks history scanning. GitHub Actions
major tags and base-image tags must be reviewed and pinned according to the
publisher's release policy before a high-assurance release.

Report vulnerabilities privately as described in `SECURITY.md`. Rotate a
credential in its owning service if exposure is suspected; never include the
credential in the report, a command, or a log.
