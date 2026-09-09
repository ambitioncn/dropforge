from __future__ import annotations

import json
import subprocess
import time
import uuid
from collections.abc import Callable
from typing import Any

from ..models import DropTarget, Product
from .base import AdapterBlocked, AdapterError
from .shopify import product_from_shopify


DISCOVERY_SCRIPT = r"""
const blockedSelector = [
  'iframe[src*="captcha" i]',
  'iframe[src*="challenge" i]',
  '[data-sitekey]',
  '#challenge-form',
  'form[action*="/password"]'
].join(',');
const path = location.pathname.toLowerCase();
const title = document.title.toLowerCase();
const challenged = Boolean(document.querySelector(blockedSelector)) ||
  ['/challenge', '/checkpoint', '/password'].some((part) => path.includes(part)) ||
  ['attention required', 'just a moment', 'verify you are human'].some((part) => title.includes(part));
if (challenged) {
  return {dropforgeVersion: 1, status: 'blocked', reason: 'browser challenge or storefront password detected'};
}
let response;
try {
  response = await fetch('/products.json?limit=250', {
    method: 'GET',
    credentials: 'same-origin',
    headers: {'Accept': 'application/json'}
  });
} catch (_error) {
  return {dropforgeVersion: 1, status: 'error', reason: 'browser product request failed'};
}
if ([401, 403, 429].includes(response.status)) {
  return {dropforgeVersion: 1, status: 'blocked', reason: `browser product request returned HTTP ${response.status}`};
}
if (!response.ok) {
  return {dropforgeVersion: 1, status: 'error', reason: `browser product request returned HTTP ${response.status}`};
}
try {
  const payload = await response.json();
  return {
    dropforgeVersion: 1,
    status: 'ok',
    products: Array.isArray(payload.products) ? payload.products : []
  };
} catch (_error) {
  return {dropforgeVersion: 1, status: 'error', reason: 'browser product response was not JSON'};
}
""".strip()


Runner = Callable[[list[str], float], subprocess.CompletedProcess[str]]


def _default_runner(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _find_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            return _find_payload(json.loads(value))
        except json.JSONDecodeError:
            return None
    if isinstance(value, dict):
        if value.get("dropforgeVersion") == 1 and isinstance(value.get("status"), str):
            return value
        for key in ("result", "value", "data"):
            if key in value:
                found = _find_payload(value[key])
                if found is not None:
                    return found
    return None


def _find_target_handle(value: Any) -> str | None:
    if isinstance(value, str):
        try:
            return _find_target_handle(json.loads(value))
        except json.JSONDecodeError:
            return None
    if isinstance(value, dict):
        for key in ("suggestedTargetId", "tabId", "targetId"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        for key in ("result", "value", "data"):
            if key in value:
                found = _find_target_handle(value[key])
                if found is not None:
                    return found
    return None


def _find_labeled_target_handle(value: Any, label: str) -> str | None:
    if isinstance(value, str):
        try:
            return _find_labeled_target_handle(json.loads(value), label)
        except json.JSONDecodeError:
            return None
    if isinstance(value, dict):
        tabs = value.get("tabs")
        if isinstance(tabs, list):
            for tab in tabs:
                if isinstance(tab, dict) and tab.get("label") == label:
                    return _find_target_handle(tab)
        for key in ("result", "value", "data"):
            if key in value:
                found = _find_labeled_target_handle(value[key], label)
                if found is not None:
                    return found
    return None


class OpenClawBrowserClient:
    """Small, non-shelling wrapper around the local OpenClaw browser CLI."""

    def __init__(
        self,
        *,
        profile: str | None = None,
        timeout: float = 30,
        runner: Runner = _default_runner,
    ):
        self.profile = profile
        self.timeout = timeout
        self.runner = runner

    def _args(self, *command: str) -> list[str]:
        args = ["openclaw", "browser", "--json", "--timeout", str(round(self.timeout * 1000))]
        if self.profile:
            args.extend(("--browser-profile", self.profile))
        args.extend(command)
        return args

    def _run(self, *command: str) -> Any:
        operation = command[0] if command and command[0].isalpha() else "command"
        try:
            completed = self.runner(self._args(*command), self.timeout + 5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdapterError(
                f"OpenClaw browser {operation} unavailable: {type(exc).__name__}"
            ) from exc
        if completed.returncode != 0:
            raise AdapterError(
                f"OpenClaw browser {operation} failed with exit {completed.returncode}"
            )
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise AdapterError("OpenClaw browser returned invalid JSON") from exc
        if isinstance(payload, dict) and payload.get("ok") is False:
            raise AdapterError("OpenClaw browser command reported failure")
        return payload

    def _close_owned_tab(self, target_handle: str) -> None:
        last_error: AdapterError | None = None
        for delay in (0.0, 0.25, 0.75):
            if delay:
                time.sleep(delay)
            try:
                self._run("close", target_handle)
                return
            except AdapterError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error

    def discover(self, store: str, label: str) -> dict[str, Any]:
        completed = False
        target_handle: str | None = None
        try:
            try:
                opened = self._run("open", store, "--label", label)
                target_handle = _find_target_handle(opened)
            except AdapterError as open_error:
                try:
                    target_handle = _find_labeled_target_handle(self._run("tabs"), label)
                except AdapterError:
                    raise open_error
                if target_handle is None:
                    raise open_error
            if target_handle is None:
                raise AdapterError("OpenClaw browser open returned no target handle")
            raw = None
            last_error: AdapterError | None = None
            for delay in (0.0, 0.5):
                if delay:
                    time.sleep(delay)
                try:
                    raw = self._run(
                        "evaluate",
                        "--target-id",
                        target_handle,
                        "--timeout-ms",
                        str(round(self.timeout * 1000)),
                        "--fn",
                        DISCOVERY_SCRIPT,
                    )
                    break
                except AdapterError as exc:
                    last_error = exc
            if raw is None:
                assert last_error is not None
                raise last_error
            payload = _find_payload(raw)
            if payload is None:
                raise AdapterError("OpenClaw browser returned no discovery payload")
            completed = True
            return payload
        finally:
            try:
                if target_handle is not None:
                    self._close_owned_tab(target_handle)
            except AdapterError:
                if completed:
                    raise


class OpenClawBrowserAdapter:
    """Read-only Shopify discovery through a managed browser session."""

    def __init__(self, client: OpenClawBrowserClient):
        self.client = client

    def discover(self, target: DropTarget) -> list[Product]:
        label = f"dropforge-{target.id}-{uuid.uuid4().hex[:8]}"
        payload = self.client.discover(target.store, label)
        status = payload.get("status")
        if status == "blocked":
            raise AdapterBlocked(str(payload.get("reason") or "browser discovery was blocked"))
        if status != "ok":
            raise AdapterError(str(payload.get("reason") or "browser discovery failed"))
        products = payload.get("products")
        if not isinstance(products, list) or not all(isinstance(item, dict) for item in products):
            raise AdapterError("browser discovery returned invalid products")
        return [product_from_shopify(item, target.store) for item in products]
