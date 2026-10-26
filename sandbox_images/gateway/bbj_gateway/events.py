"""Gateway evidence protocol (version 1).

The gateway writes one JSON object per line to **stdout**. Only the gateway
writes there; the target agent runs in a different container and cannot.
Every line carries the per-execution nonce so the collector can reject any
line it did not expect. Fields common to all events::

    {"bbj": "<nonce>", "v": 1, "seq": <int, starts at 1>, "ts": "<ISO-8601 UTC>",
     "type": "<event type>", ...}

Event types and their extra fields:

- ``gateway_ready``        {"port": int}
- ``state_snapshot``       {"phase": "before" | "after", "state": {table: [rows]}}
- ``tool_call``            {"call_seq": int, "tool": str, "args": object | null,
                            "response": object, "outcome": "ok" | "error" |
                            "unknown_tool" | "invalid_args", "side_effect": bool}
- ``http_request_rejected`` {"method": str, "path": str}
- ``gateway_error``        {"error": str}
- ``gateway_stopped``      {"reason": str, "tool_calls": int}
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

PROTOCOL_VERSION = 1
MAX_STRING_CHARS = 4096  # per string value inside an event


def _clip(value: Any) -> Any:
    """Cap string sizes so a hostile agent cannot bloat evidence."""
    if isinstance(value, str):
        return (
            value if len(value) <= MAX_STRING_CHARS else value[:MAX_STRING_CHARS] + "…[truncated]"
        )
    if isinstance(value, dict):
        return {str(k)[:128]: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value


class EventSink:
    """Serialises evidence events as nonce-tagged JSON lines."""

    def __init__(self, nonce: str, write: Callable[[str], None]) -> None:
        self._nonce = nonce
        self._write = write
        self._seq = 0

    def emit(self, event_type: str, **fields: Any) -> None:
        self._seq += 1
        event = {
            "bbj": self._nonce,
            "v": PROTOCOL_VERSION,
            "seq": self._seq,
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "type": event_type,
            **_clip(fields),
        }
        self._write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
