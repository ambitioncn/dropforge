from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import DropTarget, MatchRule


@dataclass(frozen=True)
class ServiceConfig:
    state_path: Path
    max_concurrency: int = 8
    request_timeout_seconds: float = 12
    user_agent: str = "DropForge/0.1"


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
    )
    if service.max_concurrency < 1 or service.max_concurrency > 64:
        raise ValueError("max_concurrency must be between 1 and 64")
    if service.request_timeout_seconds <= 0:
        raise ValueError("request_timeout_seconds must be positive")

    targets: list[DropTarget] = []
    for item in raw.get("drops", []):
        match_raw = item.get("match", {})
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
                sizes=_tuple(match_raw.get("sizes")),
                max_unit_price_cents=(
                    int(match_raw["max_unit_price_cents"])
                    if match_raw.get("max_unit_price_cents") is not None
                    else None
                ),
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
