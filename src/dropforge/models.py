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


def variant_matches_size(variant: Variant, size: str) -> bool:
    wanted = str(size).strip()
    raw = " ".join((variant.title, *variant.options))
    if re.fullmatch(r"\d+(?:\.\d+)?", wanted):
        raw = re.sub(
            r"(?<!\d)(\d+)\s*1/2(?!\d)",
            lambda match: f"{match.group(1)}.5",
            raw,
        )
        values = re.findall(r"(?<!\d)(\d+(?:\.\d+)?)(?!\d)", raw)
        return any(float(value) == float(wanted) for value in values)
    key = normalize(wanted)
    return key == variant.searchable_text or key in variant.searchable_text.split()


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
    title_any_contains: tuple[str, ...] = ()
    all_products: bool = False
    sizes: tuple[str, ...] = ()
    max_unit_price_cents: int | None = None

    def validate(self) -> None:
        if not self.title and not self.title_contains and not self.title_any_contains and not self.all_products:
            raise ValueError("match requires title or title_contains")
        if self.all_products and (self.title or self.title_contains or self.title_any_contains):
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
        if not all(normalize(term) in title for term in self.title_contains):
            return False
        return not self.title_any_contains or any(
            normalize(term) in title for term in self.title_any_contains
        )

    def matching_variants(self, product: Product) -> list[Variant]:
        result: list[Variant] = []
        for variant in product.variants:
            if not variant.available:
                continue
            if self.max_unit_price_cents is not None and variant.price_cents > self.max_unit_price_cents:
                continue
            if self.sizes and not any(variant_matches_size(variant, size) for size in self.sizes):
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
    purchase: "StandingPurchasePolicy | None" = None

    def validate(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", self.id):
            raise ValueError(f"invalid drop id: {self.id!r}")
        parsed = urlparse(self.store)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"drop {self.id}: store must be an https URL")
        if self.interval_seconds < 5:
            raise ValueError(f"drop {self.id}: interval_seconds must be >= 5")
        self.match.validate()
        if self.purchase is not None:
            self.purchase.validate()
            matched = {normalize(size) for size in self.match.sizes}
            purchased = {normalize(size) for size in self.purchase.sizes}
            if not matched or not purchased.issubset(matched):
                raise ValueError("purchase sizes must be a subset of monitored sizes")
            if (
                self.match.max_unit_price_cents is None
                or self.match.max_unit_price_cents > self.purchase.max_all_in_per_unit_cents
            ):
                raise ValueError("monitored unit-price ceiling must not exceed purchase ceiling")


@dataclass(frozen=True)
class StandingPurchasePolicy:
    sizes: tuple[str, ...]
    quantity_per_product: int
    max_all_in_per_unit_cents: int
    currency: str
    runner_path: str
    secret_file: str

    def validate(self) -> None:
        if not self.sizes or any(normalize(size) in {"", "any", "all"} for size in self.sizes):
            raise ValueError("purchase sizes must be explicit")
        if self.quantity_per_product < 1 or self.quantity_per_product > 10:
            raise ValueError("quantity_per_product must be between 1 and 10")
        if self.max_all_in_per_unit_cents <= 0:
            raise ValueError("max_all_in_per_unit_cents must be positive")
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("purchase currency must be an ISO 4217 code")
        for value, label in ((self.runner_path, "runner_path"), (self.secret_file, "secret_file")):
            if not value.startswith("/") or "\x00" in value:
                raise ValueError(f"purchase {label} must be an absolute path")


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
