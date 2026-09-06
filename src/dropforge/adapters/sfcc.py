from __future__ import annotations

import html
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from ..models import DropTarget, Product, Variant
from .base import AdapterBlocked, AdapterError


_PRODUCT_START = re.compile(
    r'(?=<div\s+class="[^"]*\bproduct\b[^"]*"[^>]*\bdata-pid="[^"]+")',
    re.IGNORECASE,
)
_PID = re.compile(r'<div\s+class="[^"]*\bproduct\b[^"]*"[^>]*\bdata-pid="([^"]+)"', re.IGNORECASE)
_METADATA = re.compile(r'<span\s+class="[^"]*\bproduct-metadata\b[^"]*"([^>]*)>', re.IGNORECASE)
_ATTRIBUTE = re.compile(r'\b(data-[a-z0-9_-]+)="([^"]*)"', re.IGNORECASE)
_PDP_LINK = re.compile(
    r'<a\s+class="[^"]*\bpdp-link-image\b[^"]*"[^>]*\bhref="([^"]+)"',
    re.IGNORECASE,
)
_PLP_PRICE = re.compile(r'aria-label="price-\$([0-9][0-9,.]*)', re.IGNORECASE)
_SOLD_OUT = re.compile(r'class="[^"]*\bsoldout\b|\bout\s+of\s+stock\b', re.IGNORECASE)
_PDP = re.compile(
    r'<div\s+class="[^"]*\bproduct-detail\b[^"]*"[^>]*\bdata-pid="([^"]+)"',
    re.IGNORECASE,
)
_ADD_TO_CART = re.compile(r'<button\s+class="[^"]*\badd-to-cart\b', re.IGNORECASE)


def _price_cents(value: str) -> int:
    normalized = re.sub(r"[^0-9.]", "", value.replace(",", ""))
    if not normalized:
        return 0
    return round(float(normalized) * 100)


def products_from_sfcc_category(markup: str, category_url: str) -> list[Product]:
    products: list[Product] = []
    seen: set[str] = set()
    for chunk in _PRODUCT_START.split(markup):
        pid_match = _PID.search(chunk)
        metadata_match = _METADATA.search(chunk)
        if not pid_match or not metadata_match:
            continue
        pid = html.unescape(pid_match.group(1)).strip()
        if not pid or pid in seen:
            continue
        attributes = {
            key.casefold(): html.unescape(value).strip()
            for key, value in _ATTRIBUTE.findall(metadata_match.group(1))
        }
        title = attributes.get("data-name", "")
        link_match = _PDP_LINK.search(chunk)
        if not title or not link_match:
            continue
        product_url = urljoin(category_url, html.unescape(link_match.group(1)))
        price = attributes.get("data-price", "")
        if not price:
            price_match = _PLP_PRICE.search(chunk)
            price = price_match.group(1) if price_match else ""
        seen.add(pid)
        products.append(Product(
            id=pid,
            title=title,
            handle=pid,
            url=product_url,
            variants=(Variant(
                id=pid,
                title="default",
                available=_SOLD_OUT.search(chunk) is None,
                price_cents=_price_cents(price),
            ),),
        ))
    if products:
        return products

    # Some official navigation entries resolve directly to a single PDP rather
    # than a category grid (for example, the current scarf route).
    pid_match = _PDP.search(markup)
    metadata_match = _METADATA.search(markup)
    if not pid_match or not metadata_match:
        return []
    attributes = {
        key.casefold(): html.unescape(value).strip()
        for key, value in _ATTRIBUTE.findall(metadata_match.group(1))
    }
    pid = html.unescape(pid_match.group(1)).strip()
    title = attributes.get("data-name", "")
    if not pid or not title:
        return []
    return [Product(
        id=pid,
        title=title,
        handle=pid,
        url=category_url,
        variants=(Variant(
            id=pid,
            title="default",
            available=_ADD_TO_CART.search(markup) is not None and _SOLD_OUT.search(markup) is None,
            price_cents=_price_cents(attributes.get("data-price", "")),
        ),),
    )]


class SalesforceCommerceCloudCategoryAdapter:
    """Read-only discovery for public Salesforce Commerce Cloud category pages."""

    def __init__(self, *, timeout: float = 12, user_agent: str = "DropForge/0.1"):
        self.timeout = timeout
        self.user_agent = user_agent

    def _html(self, url: str) -> str:
        request = Request(url, headers={"Accept": "text/html", "User-Agent": self.user_agent})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                content = response.read(8_000_001)
                if len(content) > 8_000_000:
                    raise AdapterError("SFCC category response exceeded size limit")
                return content.decode(response.headers.get_content_charset() or "utf-8", "replace")
        except HTTPError as exc:
            if exc.code in {401, 403, 429}:
                raise AdapterBlocked(f"SFCC returned HTTP {exc.code}") from exc
            raise AdapterError(f"SFCC returned HTTP {exc.code}") from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise AdapterError(type(exc).__name__) from exc

    def discover(self, target: DropTarget) -> list[Product]:
        markup = self._html(target.store)
        products = products_from_sfcc_category(markup, target.store)
        if not products:
            raise AdapterBlocked("SFCC category returned no recognizable product tiles")
        return products
