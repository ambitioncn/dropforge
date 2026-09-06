from __future__ import annotations

import json
import os
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .models import Observation


class NotificationError(RuntimeError):
    """A sanitized notification failure safe to expose to an operator."""


class FeishuWebhookSink:
    def __init__(
        self,
        webhook_env: str,
        *,
        timeout: float = 10,
        opener: Callable = urlopen,
        environ: dict[str, str] | None = None,
    ):
        self.webhook_env = webhook_env
        self.timeout = timeout
        self.opener = opener
        self.environ = os.environ if environ is None else environ

    def __call__(self, observation: Observation) -> None:
        webhook = self.environ.get(self.webhook_env)
        if not webhook:
            raise NotificationError(f"Feishu webhook environment reference {self.webhook_env} is unset")
        if not webhook.startswith("https://"):
            raise NotificationError("Feishu webhook must use HTTPS")
        parsed = urlparse(webhook)
        if (
            parsed.hostname not in {"open.feishu.cn", "open.larksuite.com"}
            or not parsed.path.startswith("/open-apis/bot/v2/hook/")
            or parsed.username or parsed.password
        ):
            raise NotificationError("Feishu webhook host or path is not allowed")
        candidates = observation.candidates
        summary = (
            f"DropForge: {observation.target_id} is {observation.status}; "
            f"candidates={len(candidates)}"
        )
        payload = json.dumps({"msg_type": "text", "content": {"text": summary}}).encode()
        request = Request(
            webhook,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "DropForge/0.1"},
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                if not 200 <= response.status < 300:
                    raise NotificationError(f"Feishu webhook returned HTTP {response.status}")
                # Bound the response read; never expose its possibly sensitive body.
                body = response.read(4096)
                try:
                    result = json.loads(body)
                except (ValueError, TypeError):
                    raise NotificationError("Feishu webhook returned an invalid response") from None
                if not isinstance(result, dict):
                    raise NotificationError("Feishu webhook returned an invalid response")
                code = result.get("code", result.get("StatusCode"))
                if code != 0:
                    raise NotificationError("Feishu webhook rejected the notification")
        except NotificationError:
            raise
        except HTTPError as exc:
            raise NotificationError(f"Feishu webhook returned HTTP {exc.code}") from None
        except (URLError, OSError, TimeoutError):
            raise NotificationError("Feishu webhook delivery failed") from None


class FanoutSink:
    def __init__(self, *sinks: Callable[[Observation], None]):
        self.sinks = sinks

    def __call__(self, observation: Observation) -> None:
        failures = []
        for sink in self.sinks:
            try:
                sink(observation)
            except NotificationError as exc:
                failures.append(str(exc))
        if failures:
            raise NotificationError("; ".join(failures))
