# Operations and deployment

## Preflight

Run these checks after every configuration or package change:

```bash
dropforge --config drops.toml validate
dropforge --config drops.toml status
dropforge --config drops.toml once
```

`once` performs network reads but never sends Feishu notifications or submits a
checkout. Review its JSON before starting continuous monitoring.

## Long-running modes

`watch` emits transition-only JSON (and optional notifications). `dashboard`
runs the same watcher plus the local operator UI. Do not run both against one
state database concurrently. SQLite and the idempotency ledger are local
single-host state; do not place them on shared network storage.

The dashboard address is `http://127.0.0.1:8765` by default. Use a local SSH
forward or another authenticated host-local tunnel if remote administration is
required. Never expose it through a public reverse proxy.

## systemd

After following the installation guide:

```bash
systemctl --user status dropforge.service
journalctl --user -u dropforge.service --since today
systemctl --user restart dropforge.service
```

The unit validates configuration before start, uses a restrictive umask,
protects the filesystem, and grants writes only to the state directory. It
still needs outbound storefront access and, for browser mode, read access to
the operator's existing OpenClaw configuration. Never paste secrets into unit
arguments or `systemctl` commands. The unit deliberately avoids executable-
memory restrictions because the optional OpenClaw Node.js client may require
JIT memory; the gateway remains a separate operator-managed process.

## Container

`compose.yaml` runs unprivileged, read-only, without capabilities, with
`no-new-privileges`, a small temporary filesystem, a read-only configuration,
and a private named state volume. Its host-network mode is intentional: it
preserves literal loopback binding without publishing a port.

The stock image supports public `shopify` monitoring. It intentionally does not
contain OpenClaw, a browser profile, cookies, or host sockets. Use the systemd
deployment for browser-assisted operation unless you have separately reviewed
a least-privilege OpenClaw integration. Never mount an entire home directory or
browser profile into the container.

## Backup, upgrade, and rollback

1. Stop the service cleanly.
2. Copy the SQLite database, including any `-wal` and `-shm` files, and any
   checkout ledger as private mode-0600 data.
3. Install or build the reviewed release artifact.
4. Run `validate`, then `once`, then restart the service.
5. Confirm `status`, dashboard loopback binding, and recent events.

Rollback by stopping the service, restoring both code and the matching private
state backup, validating, and restarting. Never delete an ambiguous checkout
record to force a retry; reconcile `result_unknown` first.

## Incident response

Stop the affected target before stopping the whole service. Preserve private
state and sanitized logs. If a webhook or account may be compromised, revoke it
in its owning system; do not post it in an issue. A `manual_auth_required` or
`result_unknown` checkout is an operator gate, not a retry signal.
