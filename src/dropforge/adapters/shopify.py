from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ..models import DropTarget, Product, Variant
from .base import AdapterBlocked, AdapterError


def _price_cents(value: Any) -> int:
    if isinstance(value, int):
        return value
    return round(float(str(value or "0").replace(",", "").strip()) * 100)


def product_from_shopify(raw: dict[str, Any], store: str) -> Product:
    """Normalize a Shopify product returned by either discovery transport."""
    handle = str(raw.get("handle", ""))
    variants = []
    for item in raw.get("variants", []) or []:
        options = tuple(
            str(item[key]) for key in ("option1", "option2", "option3") if item.get(key)
        )
        variants.append(
            Variant(
                id=str(item.get("id", "")),
                title=str(item.get("title", "")),
                available=item.get("available") is True,
                price_cents=_price_cents(item.get("price", raw.get("price", 0))),
                options=options,
            )
        )
    return Product(
        id=str(raw.get("id", handle)),
        title=str(raw.get("title", "")),
        handle=handle,
        url=f"{store}/products/{handle}",
        variants=tuple(variants),
    )


class ShopifyAdapter:
    """Read-only Shopify discovery using documented public storefront data."""

    def __init__(self, *, timeout: float = 12, user_agent: str = "DropForge/0.1"):
        self.timeout = timeout
        self.user_agent = user_agent

    def _json(self, url: str) -> Any:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": self.user_agent})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code in {401, 403, 429}:
                raise AdapterBlocked(f"Shopify returned HTTP {exc.code}") from exc
            raise AdapterError(f"Shopify returned HTTP {exc.code}") from exc
        except json.JSONDecodeError as exc:
            raise AdapterBlocked("Shopify returned a non-JSON access page") from exc
        except (URLError, TimeoutError) as exc:
            raise AdapterError(type(exc).__name__) from exc

    def _product(self, raw: dict[str, Any], store: str) -> Product:
        return product_from_shopify(raw, store)

    def discover(self, target: DropTarget) -> list[Product]:
        errors: list[Exception] = []
        try:
            payload = self._json(f"{target.store}/products.json?limit=250")
            products = payload.get("products", []) if isinstance(payload, dict) else []
            return [self._product(item, target.store) for item in products]
        except AdapterError as exc:
            errors.append(exc)

        if target.query:
            params = urlencode({
                "q": target.query,
                "resources[type]": "product",
                "resources[limit]": "10",
            })
            try:
                payload = self._json(f"{target.store}/search/suggest.json?{params}")
                resources = payload.get("resources", {}).get("results", {}).get("products", [])
                result = []
                for hint in resources:
                    handle = hint.get("handle") or str(hint.get("url", "")).rstrip("/").split("/")[-1]
                    if not handle:
                        continue
                    detail = self._json(f"{target.store}/products/{quote(str(handle))}.js")
                    result.append(self._product(detail, target.store))
                return result
            except AdapterError as exc:
                errors.append(exc)

        if any(isinstance(error, AdapterBlocked) for error in errors):
            raise AdapterBlocked("public Shopify discovery is unavailable; a browser adapter is required")
        raise AdapterError("Shopify discovery failed")
