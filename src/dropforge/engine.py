from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable

from .adapters.base import Adapter, AdapterBlocked, AdapterError
from .models import Candidate, DropTarget, Observation
from .state import StateStore


class MonitorEngine:
    def __init__(
        self,
        *,
        adapters: dict[str, Adapter],
        state: StateStore,
        max_concurrency: int = 8,
        error_confirmations: int = 3,
        on_change: Callable[[Observation], None] | None = None,
    ):
        self.adapters = adapters
        self.state = state
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.error_confirmations = error_confirmations
        self.on_change = on_change

    async def check(self, target: DropTarget) -> Observation:
        async with self.semaphore:
            checked_at = time.time()
            adapter = self.adapters.get(target.adapter)
            if adapter is None:
                observation = Observation(target.id, "error", checked_at, detail="unknown adapter")
            else:
                try:
                    products = await asyncio.to_thread(adapter.discover, target)
                    candidates = tuple(sorted((
                        Candidate(target.id, target.store, product, variant)
                        for product in products
                        if target.match.matches_product(product)
                        for variant in target.match.matching_variants(product)
                    ), key=lambda item: item.key))
                    observation = Observation(
                        target.id,
                        "available" if candidates else "unavailable",
                        checked_at,
                        candidates,
                    )
                except AdapterBlocked as exc:
                    observation = Observation(target.id, "blocked", checked_at, detail=str(exc))
                except AdapterError as exc:
                    observation = Observation(target.id, "error", checked_at, detail=str(exc))
                except Exception as exc:
                    observation = Observation(target.id, "error", checked_at, detail=type(exc).__name__)
            if self.state.record(
                observation,
                error_confirmations=self.error_confirmations,
            ) and self.on_change:
                await asyncio.to_thread(self.on_change, observation)
            return observation

    async def once(self, targets: tuple[DropTarget, ...]) -> list[Observation]:
        return list(await asyncio.gather(*(
            self.check(t) for t in targets
            if self.state.target_enabled(t.id, default=t.enabled)
        )))

    async def watch_target(self, target: DropTarget) -> None:
        while True:
            if not self.state.target_enabled(target.id, default=target.enabled):
                await asyncio.sleep(min(1.0, target.interval_seconds))
                continue
            started = time.monotonic()
            await self.check(target)
            elapsed = time.monotonic() - started
            jitter = random.uniform(0, min(1.0, target.interval_seconds * 0.05))
            await asyncio.sleep(max(0.1, target.interval_seconds - elapsed + jitter))

    async def watch(self, targets: tuple[DropTarget, ...]) -> None:
        await asyncio.gather(*(self.watch_target(t) for t in targets))
