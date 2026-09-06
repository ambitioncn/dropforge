from .base import Adapter, AdapterBlocked, AdapterError
from .fallback import BlockedFallbackAdapter
from .openclaw_browser import OpenClawBrowserAdapter, OpenClawBrowserClient
from .shopify import ShopifyAdapter
from .sfcc import SalesforceCommerceCloudCategoryAdapter

__all__ = [
    "Adapter",
    "AdapterBlocked",
    "AdapterError",
    "BlockedFallbackAdapter",
    "OpenClawBrowserAdapter",
    "OpenClawBrowserClient",
    "ShopifyAdapter",
    "SalesforceCommerceCloudCategoryAdapter",
]
