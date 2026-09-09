from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import DropTarget, MatchRule, StandingPurchasePolicy


@dataclass(frozen=True)
class ServiceConfig:
    state_path: Path
    max_concurrency: int = 8
    request_timeout_seconds: float = 12
    user_agent: str = "DropForge/0.1"
    browser_profile: str | None = None
    browser_timeout_seconds: float = 30
    error_confirmations: int = 3
    feishu_webhook_env: str | None = None
    feishu_timeout_seconds: float = 10
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8765
    openclaw_notify_channel: str | None = None
    openclaw_notify_target: str | None = None


@dataclass(frozen=True)
class Config:
    service: ServiceConfig
    drops: tuple[DropTarget, ...]


def _tuple(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError("list fields must contain only strings")
    return tuple(raw)


def load_config(path: Path) -> Config:
    raw = tomllib.loads(path.read_text())
    service_raw = raw.get("service", {})
    state_path = Path(service_raw.get("state_path", ".dropforge/state.db"))
    if not state_path.is_absolute():
        state_path = (path.parent / state_path).resolve()
    service = ServiceConfig(
        state_path=state_path,
        max_concurrency=int(service_raw.get("max_concurrency", 8)),
        request_timeout_seconds=float(service_raw.get("request_timeout_seconds", 12)),
        user_agent=str(service_raw.get("user_agent", "DropForge/0.1")),
        browser_profile=(
            str(service_raw["browser_profile"]) if service_raw.get("browser_profile") else None
        ),
        browser_timeout_seconds=float(service_raw.get("browser_timeout_seconds", 30)),
        error_confirmations=int(service_raw.get("error_confirmations", 3)),
        feishu_webhook_env=(
            str(service_raw["feishu_webhook_env"])
            if service_raw.get("feishu_webhook_env") else None
        ),
        feishu_timeout_seconds=float(service_raw.get("feishu_timeout_seconds", 10)),
        dashboard_host=str(service_raw.get("dashboard_host", "127.0.0.1")),
        dashboard_port=int(service_raw.get("dashboard_port", 8765)),
        openclaw_notify_channel=(
            str(service_raw["openclaw_notify_channel"])
            if service_raw.get("openclaw_notify_channel") else None
        ),
        openclaw_notify_target=(
            str(service_raw["openclaw_notify_target"])
            if service_raw.get("openclaw_notify_target") else None
        ),
    )
    if service.max_concurrency < 1 or service.max_concurrency > 64:
        raise ValueError("max_concurrency must be between 1 and 64")
    if service.request_timeout_seconds <= 0:
        raise ValueError("request_timeout_seconds must be positive")
    if service.browser_timeout_seconds <= 0:
        raise ValueError("browser_timeout_seconds must be positive")
    if service.error_confirmations < 1 or service.error_confirmations > 10:
        raise ValueError("error_confirmations must be between 1 and 10")
    if service.feishu_timeout_seconds <= 0:
        raise ValueError("feishu_timeout_seconds must be positive")
    if service.feishu_webhook_env and not re.fullmatch(
        r"[A-Z_][A-Z0-9_]{0,127}", service.feishu_webhook_env
    ):
        raise ValueError("feishu_webhook_env must be an uppercase environment variable name")
    if service.dashboard_host not in {"127.0.0.1", "::1"}:
        raise ValueError("dashboard_host must be the loopback address 127.0.0.1 or ::1")
    if service.dashboard_port < 1 or service.dashboard_port > 65535:
        raise ValueError("dashboard_port must be between 1 and 65535")
    if bool(service.openclaw_notify_channel) != bool(service.openclaw_notify_target):
        raise ValueError("OpenClaw notification channel and target must be configured together")
    if service.openclaw_notify_channel and not re.fullmatch(
        r"[a-z][a-z0-9_-]{1,31}", service.openclaw_notify_channel
    ):
        raise ValueError("invalid OpenClaw notification channel")
    if service.openclaw_notify_target and not re.fullmatch(
        r"[A-Za-z0-9:_@.-]{2,160}", service.openclaw_notify_target
    ):
        raise ValueError("invalid OpenClaw notification target")

    targets: list[DropTarget] = []
    for item in raw.get("drops", []):
        match_raw = item.get("match", {})
        purchase_raw = item.get("purchase")
        target = DropTarget(
            id=str(item.get("id", "")),
            adapter=str(item.get("adapter", "shopify")),
            store=str(item.get("store", "")).rstrip("/"),
            query=str(item.get("query", "")),
            interval_seconds=float(item.get("interval_seconds", 15)),
            enabled=bool(item.get("enabled", True)),
            match=MatchRule(
                title=match_raw.get("title"),
                title_contains=_tuple(match_raw.get("title_contains")),
                title_any_contains=_tuple(match_raw.get("title_any_contains")),
                all_products=bool(match_raw.get("all_products", False)),
                sizes=_tuple(match_raw.get("sizes")),
                max_unit_price_cents=(
                    int(match_raw["max_unit_price_cents"])
                    if match_raw.get("max_unit_price_cents") is not None
                    else None
                ),
            ),
            purchase=(
                StandingPurchasePolicy(
                    sizes=_tuple(purchase_raw.get("sizes")),
                    quantity_per_product=int(purchase_raw.get("quantity_per_product", 1)),
                    fallback_quantity=(
                        int(purchase_raw["fallback_quantity"])
                        if purchase_raw.get("fallback_quantity") is not None else None
                    ),
                    max_all_in_per_unit_cents=int(
                        purchase_raw.get("max_all_in_per_unit_cents", 0)
                    ),
                    currency=str(purchase_raw.get("currency", "")).upper(),
                    runner_path=str(purchase_raw.get("runner_path", "")),
                    secret_file=str(purchase_raw.get("secret_file", "")),
                )
                if isinstance(purchase_raw, dict) else None
            ),
        )
        target.validate()
        targets.append(target)
    if not targets:
        raise ValueError("at least one [[drops]] entry is required")
    ids = [target.id for target in targets]
    if len(ids) != len(set(ids)):
        raise ValueError("drop ids must be unique")
    return Config(service=service, drops=tuple(targets))
