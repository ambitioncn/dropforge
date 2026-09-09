from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path

from .adapters import (
    BlockedFallbackAdapter,
    OpenClawBrowserAdapter,
    OpenClawBrowserClient,
    ShopifyAdapter,
    SalesforceCommerceCloudCategoryAdapter,
)
from .autobuy import AutoPurchaseCoordinator
from .config import Config, load_config
from .controls import TargetController
from .dashboard import DashboardServer
from .engine import MonitorEngine
from .models import Observation
from .notifications import FeishuWebhookSink, NotificationError, OpenClawMessageSink
from .sinks import json_stdout
from .state import StateStore


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="dropforge")
    result.add_argument("--config", type=Path, default=Path("drops.toml"))
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    commands.add_parser("once")
    commands.add_parser("watch")
    commands.add_parser("status")
    for name in ("start", "stop"):
        control = commands.add_parser(name)
        control.add_argument("target_id")
    dashboard = commands.add_parser("dashboard")
    dashboard.add_argument("--host")
    dashboard.add_argument("--port", type=int)
    events = commands.add_parser("events")
    events.add_argument("--limit", type=int, default=50)
    return result


def change_sink(config: Config) -> Callable[[Observation], None]:
    notifier = (
        FeishuWebhookSink(
            config.service.feishu_webhook_env,
            timeout=config.service.feishu_timeout_seconds,
        )
        if config.service.feishu_webhook_env else None
    )

    def emit(observation: Observation) -> None:
        json_stdout(observation)
        if notifier:
            try:
                notifier(observation)
            except NotificationError as exc:
                print(f"notification error: {exc}", file=sys.stderr, flush=True)

    return emit


def make_engine(config: Config, state: StateStore, *, emit_changes: bool) -> MonitorEngine:
    shopify = ShopifyAdapter(
        timeout=config.service.request_timeout_seconds,
        user_agent=config.service.user_agent,
    )
    browser = OpenClawBrowserAdapter(OpenClawBrowserClient(
        profile=config.service.browser_profile,
        timeout=config.service.browser_timeout_seconds,
    ))
    sfcc = SalesforceCommerceCloudCategoryAdapter(
        timeout=config.service.request_timeout_seconds,
        user_agent=config.service.user_agent,
    )
    adapters = {
        "shopify": shopify,
        "shopify_browser": BlockedFallbackAdapter(shopify, browser),
        "sfcc_category": sfcc,
    }
    purchase_notifier = (
        OpenClawMessageSink(
            config.service.openclaw_notify_channel,
            config.service.openclaw_notify_target,
        )
        if config.service.openclaw_notify_channel and config.service.openclaw_notify_target
        else None
    )
    purchase_coordinator = (
        AutoPurchaseCoordinator(
            state,
            config.drops,
            config.service.state_path.parent / "purchase-intents",
            notifier=purchase_notifier,
        )
        if any(target.purchase is not None for target in config.drops)
        else None
    )
    return MonitorEngine(
        adapters=adapters,
        state=state,
        max_concurrency=config.service.max_concurrency,
        error_confirmations=config.service.error_confirmations,
        on_change=change_sink(config) if emit_changes else None,
        on_observation=purchase_coordinator if emit_changes else None,
    )


async def run_dashboard(config: Config, state: StateStore, host: str, port: int) -> None:
    controller = TargetController(state, config.drops)
    server = DashboardServer((host, port), controller)
    engine = make_engine(config, state, emit_changes=True)
    watch_task = asyncio.create_task(engine.watch(config.drops))
    try:
        print(f"DropForge dashboard listening on http://{host}:{server.server_port}", flush=True)
        await asyncio.to_thread(server.serve_forever)
    finally:
        server.shutdown()
        server.server_close()
        watch_task.cancel()
        await asyncio.gather(watch_task, return_exceptions=True)


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
            controller = TargetController(state, config.drops)
            if args.command == "status":
                print(json.dumps({"targets": controller.statuses()}, indent=2, sort_keys=True))
                return 0
            if args.command in {"start", "stop"}:
                try:
                    result = controller.set_enabled(args.target_id, args.command == "start")
                except KeyError:
                    print(f"unknown target: {args.target_id}", file=sys.stderr)
                    return 2
                print(json.dumps(result, sort_keys=True))
                return 0
            if args.command == "once":
                runner = make_engine(config, state, emit_changes=False)
                observations = asyncio.run(runner.once(config.drops))
                print(json.dumps([item.public_dict() for item in observations], sort_keys=True))
                return 0 if all(item.status != "error" for item in observations) else 2
            if args.command == "dashboard":
                host = args.host or config.service.dashboard_host
                port = args.port if args.port is not None else config.service.dashboard_port
                if not 1 <= port <= 65535:
                    raise ValueError("dashboard port must be between 1 and 65535")
                asyncio.run(run_dashboard(config, state, host, port))
                return 0
            runner = make_engine(config, state, emit_changes=True)
            asyncio.run(runner.watch(config.drops))
            return 0
        finally:
            state.close()
    except (OSError, ValueError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
