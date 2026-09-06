from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from dropforge.adapters.base import AdapterBlocked, AdapterError
from dropforge.adapters.fallback import BlockedFallbackAdapter
from dropforge.adapters.openclaw_browser import OpenClawBrowserAdapter, OpenClawBrowserClient
from dropforge.adapters.shopify import ShopifyAdapter
from dropforge.config import load_config
from dropforge.engine import MonitorEngine
from dropforge.models import DropTarget, MatchRule, Product, Variant
from dropforge.state import StateStore


def target(identifier: str = "drop-one") -> DropTarget:
    return DropTarget(
        id=identifier,
        adapter="fake",
        store="https://shop.example.com",
        query="crewneck",
        interval_seconds=5,
        match=MatchRule(title="Cactus Crewneck", sizes=("L",), max_unit_price_cents=14000),
    )


PRODUCT = Product(
    id="p1",
    title="Cactus Crewneck",
    handle="cactus-crewneck",
    url="https://shop.example.com/products/cactus-crewneck",
    variants=(
        Variant("v1", "M", True, 13500, ("M",)),
        Variant("v2", "L", True, 13500, ("L",)),
    ),
)


class FakeAdapter:
    def __init__(self, products=None, delay=0):
        self.products = products or []
        self.delay = delay

    def discover(self, _target):
        time.sleep(self.delay)
        return self.products


class RaisingAdapter:
    def __init__(self, error):
        self.error = error

    def discover(self, _target):
        raise self.error


class ModelTests(unittest.TestCase):
    def test_exact_product_and_size(self):
        rule = target().match
        self.assertTrue(rule.matches_product(PRODUCT))
        self.assertEqual([item.id for item in rule.matching_variants(PRODUCT)], ["v2"])

    def test_price_ceiling(self):
        rule = MatchRule(title="Cactus Crewneck", sizes=("L",), max_unit_price_cents=10000)
        self.assertEqual(rule.matching_variants(PRODUCT), [])

    def test_any_is_rejected(self):
        with self.assertRaises(ValueError):
            MatchRule(title="Any").validate()


class ConfigTests(unittest.TestCase):
    def test_example_config(self):
        path = Path(__file__).parents[1] / "examples" / "drops.toml"
        config = load_config(path)
        self.assertEqual(len(config.drops), 2)
        self.assertEqual(config.drops[0].match.sizes, ("L",))


class ShopifyAdapterTests(unittest.TestCase):
    def test_products_payload_is_normalized(self):
        adapter = ShopifyAdapter()
        with patch.object(adapter, "_json", return_value={
            "products": [{
                "id": 10,
                "title": "Cactus Crewneck",
                "handle": "cactus-crewneck",
                "variants": [{"id": 11, "title": "L", "available": True, "price": "135.00"}],
            }]
        }):
            products = adapter.discover(target())
        self.assertEqual(products[0].variants[0].price_cents, 13500)
        self.assertEqual(products[0].url, "https://shop.example.com/products/cactus-crewneck")

    def test_blocked_endpoints_are_reported_honestly(self):
        adapter = ShopifyAdapter()
        with patch.object(adapter, "_json", side_effect=AdapterBlocked("HTTP 403")):
            with self.assertRaises(AdapterBlocked):
                adapter.discover(target())

    def test_non_json_access_page_requires_browser(self):
        adapter = ShopifyAdapter()
        pages = [io.StringIO("challenge"), io.StringIO("challenge")]
        with patch("dropforge.adapters.shopify.urlopen", side_effect=pages):
            with self.assertRaises(AdapterBlocked):
                adapter.discover(target())


class BrowserAdapterTests(unittest.TestCase):
    def test_browser_payload_is_normalized_and_owned_tab_is_closed(self):
        calls = []

        def runner(args, _timeout):
            calls.append(args)
            if "evaluate" in args:
                payload = {"result": {"value": {
                    "dropforgeVersion": 1,
                    "status": "ok",
                    "products": [{
                        "id": 10,
                        "title": "Cactus Crewneck",
                        "handle": "cactus-crewneck",
                        "variants": [{
                            "id": 11,
                            "title": "L",
                            "available": True,
                            "price": "135.00",
                        }],
                    }],
                }}}
                return CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")
            return CompletedProcess(args, 0, stdout="{}", stderr="")

        client = OpenClawBrowserClient(profile="test-profile", runner=runner)
        products = OpenClawBrowserAdapter(client).discover(target())
        self.assertEqual(products[0].variants[0].price_cents, 13500)
        self.assertIn("open", calls[0])
        self.assertIn("evaluate", calls[1])
        self.assertIn("close", calls[2])
        self.assertIn("--browser-profile", calls[0])
        self.assertEqual(calls[1][calls[1].index("--target-id") + 1], calls[2][-1])

    def test_browser_challenge_is_reported_as_blocked(self):
        class Client:
            def discover(self, _store, _label):
                return {"dropforgeVersion": 1, "status": "blocked", "reason": "challenge"}

        with self.assertRaises(AdapterBlocked):
            OpenClawBrowserAdapter(Client()).discover(target())

    def test_cli_failure_does_not_expose_command_output(self):
        calls = []
        close_attempts = 0

        def runner(args, _timeout):
            nonlocal close_attempts
            calls.append(args)
            if "close" in args:
                close_attempts += 1
                if close_attempts == 1:
                    return CompletedProcess(args, 1, stdout="{}", stderr="busy")
                return CompletedProcess(args, 0, stdout="{}", stderr="")
            return CompletedProcess(args, 1, stdout='{"token":"private"}', stderr="private")

        client = OpenClawBrowserClient(runner=runner)
        with self.assertRaisesRegex(AdapterError, "exit 1") as caught:
            client.discover("https://shop.example.com", "owned-tab")
        self.assertNotIn("private", str(caught.exception))
        self.assertIn("open", calls[0])
        self.assertIn("close", calls[1])
        self.assertIn("close", calls[2])
        self.assertEqual(calls[2][-1], "owned-tab")

    def test_fallback_runs_only_for_blocked_primary(self):
        fallback = FakeAdapter([PRODUCT])
        adapter = BlockedFallbackAdapter(RaisingAdapter(AdapterBlocked("403")), fallback)
        self.assertEqual(adapter.discover(target()), [PRODUCT])
        with self.assertRaises(AdapterError):
            BlockedFallbackAdapter(RaisingAdapter(AdapterError("timeout")), fallback).discover(target())


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self.temp.name) / "state.db")

    async def asyncTearDown(self):
        self.state.close()
        self.temp.cleanup()

    async def test_available_transition_is_deduplicated(self):
        changes = []
        engine = MonitorEngine(
            adapters={"fake": FakeAdapter([PRODUCT])}, state=self.state, on_change=changes.append
        )
        first = await engine.check(target())
        second = await engine.check(target())
        self.assertEqual(first.status, "available")
        self.assertEqual(second.status, "available")
        self.assertEqual(len(changes), 1)
        self.assertEqual(len(self.state.events()), 1)

    async def test_targets_are_checked_concurrently(self):
        engine = MonitorEngine(adapters={"fake": FakeAdapter([], delay=0.15)}, state=self.state)
        started = time.monotonic()
        await engine.once((target("drop-one"), target("drop-two")))
        self.assertLess(time.monotonic() - started, 0.27)
