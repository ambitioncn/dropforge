from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


def normalize(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).strip()


@dataclass(frozen=True)
class Variant:
    id: str
    title: str
    available: bool
    price_cents: int
    options: tuple[str, ...] = ()

    @property
    def searchable_text(self) -> str:
        return normalize(" ".join((self.title, *self.options)))


@dataclass(frozen=True)
class Product:
    id: str
    title: str
    handle: str
    url: str
    variants: tuple[Variant, ...] = ()


@dataclass(frozen=True)
class MatchRule:
    title: str | None = None
    title_contains: tuple[str, ...] = ()
    all_products: bool = False
    sizes: tuple[str, ...] = ()
    max_unit_price_cents: int | None = None

    def validate(self) -> None:
        if not self.title and not self.title_contains and not self.all_products:
            raise ValueError("match requires title or title_contains")
        if self.all_products and (self.title or self.title_contains):
            raise ValueError("all_products cannot be combined with title filters")
        if self.title and normalize(self.title) in {"any", "all", ""}:
            raise ValueError("exact title cannot be Any/All")
        if any(normalize(size) in {"any", "all", ""} for size in self.sizes):
            raise ValueError("sizes must be explicit")
        if self.max_unit_price_cents is not None and self.max_unit_price_cents <= 0:
            raise ValueError("max_unit_price_cents must be positive")

    def matches_product(self, product: Product) -> bool:
        if self.all_products:
            return True
        title = normalize(product.title)
        if self.title and title != normalize(self.title):
            return False
        return all(normalize(term) in title for term in self.title_contains)

    def matching_variants(self, product: Product) -> list[Variant]:
        wanted = [normalize(size) for size in self.sizes]
        result: list[Variant] = []
        for variant in product.variants:
            if not variant.available:
                continue
            if self.max_unit_price_cents is not None and variant.price_cents > self.max_unit_price_cents:
                continue
            if wanted and not any(
                size == variant.searchable_text or size in variant.searchable_text.split()
                for size in wanted
            ):
                continue
            result.append(variant)
        return result


@dataclass(frozen=True)
class DropTarget:
    id: str
    adapter: str
    store: str
    query: str
    interval_seconds: float
    match: MatchRule
    enabled: bool = True

    def validate(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", self.id):
            raise ValueError(f"invalid drop id: {self.id!r}")
        parsed = urlparse(self.store)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"drop {self.id}: store must be an https URL")
        if self.interval_seconds < 5:
            raise ValueError(f"drop {self.id}: interval_seconds must be >= 5")
        self.match.validate()


@dataclass(frozen=True)
class Candidate:
    target_id: str
    store: str
    product: Product
    variant: Variant

    @property
    def key(self) -> str:
        raw = f"{self.target_id}\0{self.product.id}\0{self.variant.id}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def public_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "store": self.store,
            "product": self.product.title,
            "handle": self.product.handle,
            "product_url": self.product.url,
            "variant": self.variant.title,
            "variant_id": self.variant.id,
            "price_cents": self.variant.price_cents,
            "candidate_key": self.key,
        }


@dataclass(frozen=True)
class Observation:
    target_id: str
    status: str
    checked_at: float
    candidates: tuple[Candidate, ...] = ()
    detail: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "status": self.status,
            "checked_at": self.checked_at,
            "candidates": [item.public_dict() for item in self.candidates],
            "detail": self.detail,
        }

    @property
    def digest(self) -> str:
        payload = {
            "status": self.status,
            "candidates": [item.public_dict() for item in self.candidates],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
