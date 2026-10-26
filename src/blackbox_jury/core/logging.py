"""Structured JSON logging with mandatory secret redaction.

Every record is emitted as one JSON line carrying correlation ids from context
(``request_id``, ``run_id``, ``execution_id``) and a ``component`` field that
separates harness failures from target-agent failures (ARCHITECTURE.md §11).
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Literal

from blackbox_jury.core.redaction import Redactor

Component = Literal["api", "harness", "target", "jury"]

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
execution_id_var: ContextVar[str | None] = ContextVar("execution_id", default=None)

# Attributes present on every LogRecord; anything else was passed via ``extra=``.
_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",  # uvicorn duplicate of msg with ANSI escapes
}


class RedactingJsonFormatter(logging.Formatter):
    """Formats records as redacted JSON. Redaction runs on the complete payload."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "component": getattr(record, "component", "harness"),
            "request_id": request_id_var.get(),
            "run_id": run_id_var.get(),
            "execution_id": execution_id_var.get(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        redacted = self._redactor.redact(payload)
        return json.dumps(redacted, default=str, ensure_ascii=False)


def configure_logging(level: str, redactor: Redactor) -> None:
    """Install the redacting JSON handler on the root logger (idempotent)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_bbj", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(RedactingJsonFormatter(redactor))
    handler._bbj = True  # type: ignore[attr-defined]  # marker for idempotent reconfiguration
    root.addHandler(handler)
    root.setLevel(level)
    # Route uvicorn's own loggers through the same redacting handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
