"""RefundBot container entrypoint and HTTP tool client.

Environment (set by the sandbox runner; ARCHITECTURE.md §6):
- ``BBJ_TASK_INPUT``: base64 JSON ``{principal, user_message, mode, system_context?}``, <= 16 KiB.
- ``BBJ_GATEWAY_URL``: base URL of the tool gateway on the internal network.

Output contract: the **last line of stdout** is ``{"final_response": "<text>"}``.
Exit codes: 0 finished, 2 invalid input/config, 3 gateway unreachable.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from bbj_refundbot.agent import MODES, RefundBot, TaskInput, ToolResult

MAX_TASK_INPUT_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
TOOL_TIMEOUT_S = 5.0

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_GATEWAY_UNREACHABLE = 3


class GatewayUnreachableError(RuntimeError):
    pass


class HttpToolClient:
    def __init__(self, base_url: str, timeout_s: float = TOOL_TIMEOUT_S) -> None:
        parts = urlsplit(base_url)
        if parts.scheme != "http" or not parts.hostname:
            raise ValueError("BBJ_GATEWAY_URL must be an http:// URL")
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    def call(self, tool: str, args: dict[str, Any]) -> ToolResult:
        # Scheme is validated as http:// in __init__, so file:/custom schemes are impossible.
        req = urllib.request.Request(  # noqa: S310
            f"{self._base}/tools/{tool}",
            data=json.dumps(args).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                raw = resp.read(MAX_RESPONSE_BYTES)
        except urllib.error.HTTPError as exc:
            raw = exc.read(MAX_RESPONSE_BYTES)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise GatewayUnreachableError(str(exc)) from exc
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return ToolResult(ok=False, error_code="bad_gateway_response")
        if body.get("ok"):
            return ToolResult(ok=True, result=body.get("result") or {})
        return ToolResult(ok=False, error_code=(body.get("error") or {}).get("code"))


def parse_task_input(raw: str) -> TaskInput:
    if len(raw) > (MAX_TASK_INPUT_BYTES * 4) // 3 + 4:
        raise ValueError("BBJ_TASK_INPUT too large")
    try:
        data = json.loads(base64.b64decode(raw, validate=True))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("BBJ_TASK_INPUT must be base64-encoded JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("task input must be a JSON object")
    principal, message, mode = data.get("principal"), data.get("user_message"), data.get("mode")
    context = data.get("system_context", {})
    if not isinstance(principal, str) or not principal:
        raise ValueError("principal must be a non-empty string")
    if not isinstance(message, str) or not message:
        raise ValueError("user_message must be a non-empty string")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    if not isinstance(context, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in context.items()
    ):
        raise ValueError("system_context must map strings to strings")
    return TaskInput(principal=principal, user_message=message, mode=mode, system_context=context)


def _print(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def main(env: Mapping[str, str] | None = None, out: Callable[[str], None] = _print) -> int:
    env = os.environ if env is None else env
    try:
        task = parse_task_input(env.get("BBJ_TASK_INPUT", ""))
        client = HttpToolClient(env.get("BBJ_GATEWAY_URL", ""))
    except ValueError as exc:
        sys.stderr.write(f"refundbot: invalid input: {exc}\n")
        return EXIT_BAD_INPUT
    try:
        reply = RefundBot(client, task).run()
    except GatewayUnreachableError as exc:
        sys.stderr.write(f"refundbot: gateway unreachable: {exc}\n")
        return EXIT_GATEWAY_UNREACHABLE
    out(json.dumps({"final_response": reply}))
    return EXIT_OK
