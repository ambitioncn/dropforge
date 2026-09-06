# Security policy

## Supported versions

Only the latest release receives security fixes during the alpha phase.

## Reporting

Report vulnerabilities privately to the repository maintainers. Do not include
live credentials, card data, cookies, addresses, or checkout URLs in a report.

## Trust boundaries

DropForge core is a read-only monitor. Store adapters process untrusted remote
data and must return bounded normalized models. Notification and checkout
workers are separate trust domains and should run with the least privileges
needed. CAPTCHA, queue, 3DS, login, and payment confirmation are explicit human
handoff points.

DropForge does not provide anti-bot evasion, CAPTCHA solving, purchase-limit
bypass, fingerprint spoofing, or proxy rotation.

The guarded checkout core stores only public intent, state, opaque session
reference and order reference. It does not accept shipping addresses, browser
cookies, payment credentials or checkout URLs. Every submit authorization is
short-lived, intent-bound and consumed before the click; ambiguous outcomes
must be reconciled rather than retried.

## Operational surfaces

The dashboard is deliberately local-only and rejects wildcard, LAN, DNS-name,
and public bind addresses. Do not expose it directly through a reverse proxy.
Feishu webhook URLs are credentials: keep them in a protected environment
entry and configure only the entry name. DropForge restricts the endpoint host,
never logs webhook URLs or response bodies, and sends only target transition
summaries.

Deployment hardening, data classification, incident response, and release
checks are detailed in [docs/security.md](docs/security.md). The distributed
systemd unit and container run as an unprivileged user, keep the dashboard on
literal loopback, and do not include secrets or browser profiles.
