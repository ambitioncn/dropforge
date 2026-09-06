# Browser and human handoff

## Discovery fallback

Set `adapter = "shopify_browser"` only when the public Shopify endpoint is
known to return 401, 403, or 429. DropForge opens an owned OpenClaw tab, performs
a same-origin read-only `/products.json` request, and closes only that discovery
tab. Ordinary transport failures do not trigger browser fallback.

OpenClaw must already be installed and authenticated by the operator, and
`browser.evaluateEnabled` must be enabled. DropForge accepts a profile name,
not credentials or a profile export.

## Guarded checkout

The checkout driver uses a deterministic tab label derived from the immutable
purchase intent. Preparation replaces the cart in that tab, inspects every
line and total, and stops before submission. Submission is unavailable until
the guarded executor receives a fresh authorization for the exact intent and
verified final total.

When CAPTCHA, login, or 3DS appears:

1. DropForge returns `manual_auth_required` and keeps the same named tab open.
2. The operator focuses that tab in the existing OpenClaw browser and completes
   the challenge manually. Do not use solvers, scripts, proxy rotation, or
   fingerprint changes.
3. Resume by inspecting the same intent/session. Re-check cart, currency, and
   final total; issue a new authorization only if policy still matches.
4. If submission may already have occurred, reconcile. Never submit again from
   `result_unknown`.

The operator enters any account, shipping, or payment data directly in the
visible browser. DropForge does not accept, store, log, or transmit those
values. No real purchase should be exercised in automated tests.

## Deployment choice

The supplied container deliberately has no OpenClaw binary or profile. Use the
host systemd service for browser handoff. Adding host sockets, home-directory
mounts, or browser-profile volumes changes the threat model and requires a
separate security review.
