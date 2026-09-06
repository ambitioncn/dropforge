# Installation

## Requirements

- CPython 3.11, 3.12, 3.13, or 3.14.
- Network access to the public storefronts being monitored.
- Optional: an existing OpenClaw installation for `shopify_browser` discovery
  or guarded same-session checkout handoff.

DropForge itself has no runtime package dependencies. It does not bundle a
browser, cookies, an account, payment data, or a CAPTCHA service.

## Virtual environment

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
cp examples/drops.toml drops.toml
dropforge --config drops.toml validate
```

For development, install `.[dev]` and run the commands in `CONTRIBUTING.md`.
Avoid installing from an unreviewed branch on an operator host.

## systemd user service

The supplied unit expects a dedicated virtual environment and configuration:

```bash
python3 -m venv "$HOME/.local/share/dropforge/venv"
"$HOME/.local/share/dropforge/venv/bin/python" -m pip install .
install -d -m 0700 "$HOME/.config/dropforge" "$HOME/.local/state/dropforge"
install -m 0600 examples/drops.toml "$HOME/.config/dropforge/drops.toml"
install -d -m 0700 "$HOME/.config/systemd/user"
install -m 0644 deploy/systemd/dropforge.service "$HOME/.config/systemd/user/dropforge.service"
```

Edit `state_path` to `%h/.local/state/dropforge/state.db` using the expanded
home path (TOML does not expand `%h` or `$HOME`). Validate before enabling. The
activation commands are intentionally documented, not run by installation:

```bash
systemctl --user daemon-reload
systemctl --user enable --now dropforge.service
```

## Container image

Copy the non-secret template, edit exact matching rules, and validate it:

```bash
cp deploy/container/drops.toml.example deploy/container/drops.toml
docker compose build
docker compose run --rm dropforge --config /config/drops.toml validate
docker compose up -d
```

Compose uses Linux host networking so a process bound to `127.0.0.1` remains
reachable only from that host. It publishes no container port. Rootless Docker
or Podman is preferred. On non-Linux hosts, run `once`/`watch` in the container
or use the host virtual-environment deployment; do not change the dashboard to
a wildcard address.
