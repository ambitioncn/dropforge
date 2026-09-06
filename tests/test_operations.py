from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from textwrap import dedent
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dropforge.controls import TargetController
from dropforge.config import load_config
from dropforge.cli import main
from dropforge.dashboard import DashboardServer
from dropforge.engine import MonitorEngine
from dropforge.models import DropTarget, MatchRule, Observation
from dropforge.notifications import FeishuWebhookSink, NotificationError
from dropforge.state import StateStore


def target(identifier: str = "drop-one", *, enabled: bool = True) -> DropTarget:
    return DropTarget(
        id=identifier,
        adapter="fake",
        store="https://shop.example.test",
        query="shoe",
        interval_seconds=5,
        enabled=enabled,
        match=MatchRule(title="Exact Shoe"),
    )


class FakeAdapter:
    def __init__(self):
        self.calls = 0

    def discover(self, _target):
        self.calls += 1
        return []


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, limit):
        return b'{"code": 0}'[:limit]


class FeishuNotificationTests(unittest.TestCase):
    def test_notification_uses_secret_reference_and_minimal_payload(self):
        captured = []

        def opener(request, *, timeout):
            captured.append((request, timeout))
            return FakeResponse()

        sink = FeishuWebhookSink(
            "FEISHU_HOOK",
            opener=opener,
            environ={"FEISHU_HOOK": "https://open.feishu.cn/open-apis/bot/v2/hook/private"},
        )
        sink(Observation("drop-one", "available", 1.0))
        request, timeout = captured[0]
        payload = json.loads(request.data)
        self.assertEqual(timeout, 10)
        self.assertEqual(payload["msg_type"], "text")
        self.assertIn("drop-one is available", payload["content"]["text"])
        self.assertNotIn("private", payload["content"]["text"])

    def test_missing_or_non_feishu_webhook_is_rejected_without_leak(self):
        with self.assertRaisesRegex(NotificationError, "unset"):
            FeishuWebhookSink("HOOK", environ={})(Observation("drop-one", "error", 1))
        secret = "https://example.test/open-apis/bot/v2/hook/secret-value"
        with self.assertRaises(NotificationError) as caught:
            FeishuWebhookSink("HOOK", environ={"HOOK": secret})(
                Observation("drop-one", "error", 1)
            )
        self.assertNotIn("secret-value", str(caught.exception))


class OperationsConfigTests(unittest.TestCase):
    def config(self, service_lines: str):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "drops.toml"
        path.write_text(dedent(f"""
            [service]
            {service_lines}

            [[drops]]
            id = "drop-one"
            store = "https://shop.example.test"
            query = "shoe"
            interval_seconds = 5

            [drops.match]
            title = "Exact Shoe"
        """))
        return load_config(path)

    def test_operations_defaults_are_loopback_and_notification_off(self):
        config = self.config("")
        self.assertEqual(config.service.dashboard_host, "127.0.0.1")
        self.assertEqual(config.service.dashboard_port, 8765)
        self.assertIsNone(config.service.feishu_webhook_env)

    def test_plaintext_webhook_and_non_loopback_config_are_rejected(self):
        for line in (
            'dashboard_host = "0.0.0.0"',
            'feishu_webhook_env = "https://open.feishu.cn/open-apis/bot/v2/hook/nope"',
        ):
            with self.subTest(line=line), self.assertRaises(ValueError):
                self.config(line)


class OperationsCliTests(unittest.TestCase):
    def test_cli_stop_and_status_share_persistent_control(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "drops.toml"
            path.write_text(dedent(f"""
                [service]
                state_path = "{Path(temp) / 'state.db'}"

                [[drops]]
                id = "drop-one"
                store = "https://shop.example.test"
                query = "shoe"
                interval_seconds = 5

                [drops.match]
                title = "Exact Shoe"
            """))
            stop_output = io.StringIO()
            with redirect_stdout(stop_output):
                self.assertEqual(main(["--config", str(path), "stop", "drop-one"]), 0)
            status_output = io.StringIO()
            with redirect_stdout(status_output):
                self.assertEqual(main(["--config", str(path), "status"]), 0)
            self.assertFalse(json.loads(stop_output.getvalue())["enabled"])
            self.assertFalse(json.loads(status_output.getvalue())["targets"][0]["enabled"])


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self.temp.name) / "state.db")

    async def asyncTearDown(self):
        self.state.close()
        self.temp.cleanup()

    async def test_stop_and_start_are_persistent_and_gate_once(self):
        item = target()
        adapter = FakeAdapter()
        controller = TargetController(self.state, (item,))
        controller.set_enabled(item.id, False)
        self.assertEqual(await MonitorEngine(adapters={"fake": adapter}, state=self.state).once((item,)), [])
        self.assertEqual(adapter.calls, 0)
        self.assertFalse(controller.statuses()[0]["enabled"])

        controller.set_enabled(item.id, True)
        observations = await MonitorEngine(adapters={"fake": adapter}, state=self.state).once((item,))
        self.assertEqual(len(observations), 1)
        self.assertEqual(adapter.calls, 1)
        self.assertTrue(controller.statuses()[0]["enabled"])

    async def test_unknown_target_control_is_rejected(self):
        with self.assertRaises(KeyError):
            TargetController(self.state, (target(),)).set_enabled("other-drop", False)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self.temp.name) / "state.db")
        self.controller = TargetController(self.state, (target(),))

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def test_non_loopback_bind_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            DashboardServer(("0.0.0.0", 0), self.controller)

    def test_status_and_guarded_control_endpoint(self):
        server = DashboardServer(("127.0.0.1", 0), self.controller)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(f"{base}/api/status", timeout=2) as response:
                payload = json.load(response)
            self.assertTrue(payload["targets"][0]["enabled"])

            request = Request(
                f"{base}/api/targets/drop-one/stop",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=2)
            self.assertEqual(caught.exception.code, 403)
            self.assertTrue(self.controller.statuses()[0]["enabled"])

            request.add_header("X-DropForge-Control", "1")
            with urlopen(request, timeout=2) as response:
                result = json.load(response)
            self.assertFalse(result["enabled"])
            self.assertFalse(self.controller.statuses()[0]["enabled"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
