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
