from __future__ import annotations

import json
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .controls import TargetController


_CONTROL_PATH = re.compile(r"^/api/targets/([a-z][a-z0-9_-]{1,63})/(start|stop)$")
_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>DropForge</title><style>
body{font:16px system-ui;max-width:900px;margin:2rem auto;padding:0 1rem;background:#111;color:#eee}
table{width:100%;border-collapse:collapse}td,th{padding:.6rem;border-bottom:1px solid #444;text-align:left}
button{padding:.35rem .7rem}code{color:#9df}.muted{color:#aaa}
</style></head><body><h1>DropForge</h1><p class="muted">Local control dashboard</p>
<table><thead><tr><th>Target</th><th>Control</th><th>Last status</th><th>Action</th></tr></thead><tbody id="rows"></tbody></table>
<script>
async function refresh(){let r=await fetch('/api/status',{cache:'no-store'}), data=await r.json();
document.getElementById('rows').replaceChildren(...data.targets.map(t=>{let tr=document.createElement('tr');
for(let v of [t.target_id,t.enabled?'running':'stopped',t.observation?.status||'never checked']){let td=document.createElement('td');td.textContent=v;tr.append(td)}
let td=document.createElement('td'),b=document.createElement('button');b.textContent=t.enabled?'Stop':'Start';
b.onclick=async()=>{await fetch('/api/targets/'+encodeURIComponent(t.target_id)+'/'+(t.enabled?'stop':'start'),{method:'POST',headers:{'Content-Type':'application/json','X-DropForge-Control':'1'},body:'{}'});await refresh()};td.append(b);tr.append(td);return tr}))}
refresh();setInterval(refresh,5000);
</script></body></html>"""


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], controller: TargetController):
        host, _port = address
        if host not in {"127.0.0.1", "::1"}:
            raise ValueError("dashboard must bind to the literal loopback address 127.0.0.1 or ::1")
        self.controller = controller
        if host == "::1":
            self.address_family = socket.AF_INET6
        super().__init__(address, DashboardHandler)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer
    server_version = "DropForge"
    sys_version = ""

    def log_message(self, format: str, *args) -> None:
        # Avoid logging untrusted request paths or headers.
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "frame-ancestors 'none'; base-uri 'none'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: dict) -> None:
        self._send(status, json.dumps(value, sort_keys=True).encode(), "application/json")

    def do_GET(self) -> None:
        if self.path == "/":
            self._send(200, _HTML.encode(), "text/html; charset=utf-8")
        elif self.path == "/api/status":
            self._json(200, {"targets": self.server.controller.statuses()})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        match = _CONTROL_PATH.fullmatch(self.path)
        if not match:
            self._json(404, {"error": "not found"})
            return
        if self.headers.get("Content-Type") != "application/json" or self.headers.get("X-DropForge-Control") != "1":
            self._json(403, {"error": "control header required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "invalid content length"})
            return
        if length < 0:
            self._json(400, {"error": "invalid content length"})
            return
        if length > 1024:
            self._json(413, {"error": "request too large"})
            return
        body = self.rfile.read(length)
        try:
            if json.loads(body or b"{}") != {}:
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": "empty JSON object required"})
            return
        target_id, action = match.groups()
        try:
            result = self.server.controller.set_enabled(target_id, action == "start")
        except KeyError:
            self._json(404, {"error": "unknown target"})
            return
        self._json(200, result)
