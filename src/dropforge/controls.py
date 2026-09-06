from __future__ import annotations

from .models import DropTarget
from .state import StateStore


class TargetController:
    def __init__(self, state: StateStore, targets: tuple[DropTarget, ...]):
        self.state = state
        self.targets = {target.id: target for target in targets}

    def set_enabled(self, target_id: str, enabled: bool) -> dict:
        target = self.targets.get(target_id)
        if target is None:
            raise KeyError(target_id)
        self.state.set_target_enabled(target_id, enabled)
        return {"target_id": target_id, "enabled": enabled}

    def statuses(self) -> list[dict]:
        return self.state.target_statuses({
            target_id: target.enabled for target_id, target in self.targets.items()
        })
