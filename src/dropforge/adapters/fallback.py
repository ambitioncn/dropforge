from __future__ import annotations

from ..models import DropTarget, Product
from .base import Adapter, AdapterBlocked


class BlockedFallbackAdapter:
    """Use a secondary read-only adapter only when the primary is access-blocked."""

    def __init__(self, primary: Adapter, secondary: Adapter):
        self.primary = primary
        self.secondary = secondary

    def discover(self, target: DropTarget) -> list[Product]:
        try:
            return self.primary.discover(target)
        except AdapterBlocked:
            return self.secondary.discover(target)
