"""DropForge public package."""

from .checkout import (
    CartLine,
    CartSnapshot,
    CheckoutLedger,
    CheckoutOutcome,
    CheckoutPolicyError,
    CheckoutSnapshot,
    GuardedCheckoutExecutor,
    PurchaseAuthorization,
    PurchaseIntent,
)
from .checkout_browser import OpenClawCheckoutDriver
from .autobuy import AutoPurchaseCoordinator, DropbotCommandRunner, PurchaseRunResult
from .controls import TargetController
from .notifications import FeishuWebhookSink, NotificationError, OpenClawMessageSink
from .models import Candidate, DropTarget, MatchRule, Product, StandingPurchasePolicy, Variant

__all__ = [
    "Candidate",
    "AutoPurchaseCoordinator",
    "CartLine",
    "CartSnapshot",
    "CheckoutLedger",
    "CheckoutOutcome",
    "CheckoutPolicyError",
    "CheckoutSnapshot",
    "DropTarget",
    "DropbotCommandRunner",
    "GuardedCheckoutExecutor",
    "MatchRule",
    "FeishuWebhookSink",
    "NotificationError",
    "OpenClawCheckoutDriver",
    "OpenClawMessageSink",
    "Product",
    "PurchaseAuthorization",
    "PurchaseIntent",
    "PurchaseRunResult",
    "StandingPurchasePolicy",
    "TargetController",
    "Variant",
]
__version__ = "0.2.1"
