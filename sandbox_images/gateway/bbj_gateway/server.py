"""HTTP front end for the gateway and the container entrypoint.

Environment (set by the sandbox runner):
- ``BBJ_EXECUTION_NONCE``: 32 lowercase hex chars; tags every evidence line.
- ``BBJ_SEED_STATE``: base64-encoded JSON seed (<= 64 KiB decoded).
- ``BBJ_GATEWAY_PORT``: listen port (default 8080).

Lifecycle: emit ``state_snapshot(before)`` → bind → ``gateway_ready`` →
serve until SIGTERM/SIGINT → ``state_snapshot(after)`` → ``gateway_stopped``.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import signal
import sys
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import FrameType
from typing import Any

from bbj_gateway.events import EventSink
from bbj_gateway.store import SeedError, Store
from bbj_gateway.tools import MAX_BODY_BYTES, Gateway

MAX_SEED_BYTES = 64 * 1024
_NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
_TOOL_PATH = re.compile(r"^/tools/([^/?#]{1,128})$")

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2


class _Stop(Exception):  # noqa: N818  # control-flow signal, not an error
    """Raised from the signal handler to leave ``serve_forever``."""


def make_handler(gateway: Gateway) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "bbj-gateway/1"
        sys_version = ""
        timeout = 10  # seconds; a stalled client cannot pin the single-threaded server

        def log_message(self, format: str, *args: Any) -> None:
            return  # stdout is reserved for evidence; access logs are not needed

        def _send(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _reject(self) -> None:
            gateway.sink.emit(
                "http_request_rejected", method=self.command[:16], path=self.path[:256]
            )
            self._send(404, {"ok": False, "error": {"code": "not_found", "message": "not found"}})

        def do_POST(self) -> None:
            match = _TOOL_PATH.match(self.path)
            if not match:
                self._reject()
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0:
                body = b"\x00invalid-length"
            elif length > MAX_BODY_BYTES:
                body = b" " * (MAX_BODY_BYTES + 1)  # recorded as body_too_large; not read
                self.close_connection = True
            else:
                body = self.rfile.read(length)
            result = gateway.handle(match.group(1), body)
            self._send(result.status, result.response)

        def do_GET(self) -> None:
            self._reject()

        # Names are dictated by BaseHTTPRequestHandler's dispatch convention.
        do_PUT = do_DELETE = do_PATCH = do_HEAD = do_GET  # noqa: N815

    return Handler


def _write_stdout(line: str) -> None:
    sys.stdout.write(line)
    sys.stdout.flush()


def load_config(env: Mapping[str, str]) -> tuple[str, dict[str, Any], int]:
    nonce = env.get("BBJ_EXECUTION_NONCE", "")
    if not _NONCE_RE.match(nonce):
        raise ValueError("BBJ_EXECUTION_NONCE must be 32 lowercase hex characters")
    raw = env.get("BBJ_SEED_STATE", "")
    if len(raw) > (MAX_SEED_BYTES * 4) // 3 + 4:
        raise ValueError("BBJ_SEED_STATE too large")
    try:
        seed = json.loads(base64.b64decode(raw, validate=True))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("BBJ_SEED_STATE must be base64-encoded JSON") from exc
    port = int(env.get("BBJ_GATEWAY_PORT", "8080"))
    if not 1 <= port <= 65535:
        raise ValueError("BBJ_GATEWAY_PORT out of range")
    return nonce, seed, port


def main(
    env: Mapping[str, str] | None = None,
    write: Callable[[str], None] = _write_stdout,
) -> int:
    env = os.environ if env is None else env
    try:
        nonce, seed, port = load_config(env)
    except ValueError as exc:
        # The nonce may be the invalid part, so this line is deliberately untagged;
        # the collector will treat the execution as missing evidence.
        write(json.dumps({"bbj": None, "type": "gateway_error", "error": str(exc)}) + "\n")
        return EXIT_CONFIG_ERROR

    sink = EventSink(nonce, write)
    try:
        store = Store(seed)
    except SeedError as exc:
        sink.emit("gateway_error", error=str(exc))
        return EXIT_CONFIG_ERROR

    gateway = Gateway(store, sink)
    sink.emit("state_snapshot", phase="before", state=store.snapshot())
    # Binding all interfaces is required inside the container; the only reachable
    # interface is the per-execution internal Docker network (no egress, no host).
    server = HTTPServer(("0.0.0.0", port), make_handler(gateway))  # noqa: S104

    def _on_signal(signum: int, _frame: FrameType | None) -> None:
        raise _Stop(signal.Signals(signum).name)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    reason = "unknown"
    sink.emit("gateway_ready", port=port)
    try:
        server.serve_forever(poll_interval=0.2)
    except _Stop as stop:
        reason = str(stop)
    finally:
        server.server_close()
        sink.emit("state_snapshot", phase="after", state=store.snapshot())
        sink.emit("gateway_stopped", reason=reason, tool_calls=gateway.call_count)
    return EXIT_OK
