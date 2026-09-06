from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .adapters import ShopifyAdapter
from .config import Config, load_config
from .engine import MonitorEngine
from .sinks import json_stdout
from .state import StateStore


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="dropforge")
    result.add_argument("--config", type=Path, default=Path("drops.toml"))
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    commands.add_parser("once")
    commands.add_parser("watch")
    events = commands.add_parser("events")
    events.add_argument("--limit", type=int, default=50)
    return result


def make_engine(config: Config, state: StateStore, *, emit_changes: bool) -> MonitorEngine:
    adapters = {
        "shopify": ShopifyAdapter(
            timeout=config.service.request_timeout_seconds,
            user_agent=config.service.user_agent,
        )
    }
    return MonitorEngine(
        adapters=adapters,
        state=state,
        max_concurrency=config.service.max_concurrency,
        on_change=json_stdout if emit_changes else None,
    )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "validate":
            print(json.dumps({"ok": True, "drops": len(config.drops)}))
            return 0
        state = StateStore(config.service.state_path)
        try:
            if args.command == "events":
                print(json.dumps(state.events(args.limit), indent=2, sort_keys=True))
                return 0
            if args.command == "once":
                runner = make_engine(config, state, emit_changes=False)
                observations = asyncio.run(runner.once(config.drops))
                print(json.dumps([item.public_dict() for item in observations], sort_keys=True))
                return 0 if all(item.status != "error" for item in observations) else 2
            runner = make_engine(config, state, emit_changes=True)
            asyncio.run(runner.watch(config.drops))
            return 0
        finally:
            state.close()
    except (OSError, ValueError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
