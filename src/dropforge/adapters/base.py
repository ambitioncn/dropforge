from __future__ import annotations

from typing import Protocol

from ..models import DropTarget, Product


class AdapterError(RuntimeError):
    pass


class AdapterBlocked(AdapterError):
    pass


class Adapter(Protocol):
    def discover(self, target: DropTarget) -> list[Product]: ...
