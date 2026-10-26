from __future__ import annotations

import json
import logging
import sys

from blackbox_jury.core.logging import RedactingJsonFormatter, request_id_var
from blackbox_jury.core.redaction import Redactor

SECRET = "super-secret-token-value-xyz"


def _format(record: logging.LogRecord) -> dict[str, object]:
    out = RedactingJsonFormatter(Redactor([SECRET])).format(record)
    assert SECRET not in out
    parsed: dict[str, object] = json.loads(out)
    return parsed


def _record(msg: str, *args: object, **extra: object) -> logging.LogRecord:
    rec = logging.LogRecord("bbj.test", logging.INFO, __file__, 1, msg, args, None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_message_and_args_redacted() -> None:
    payload = _format(_record("token is %s", SECRET))
    assert payload["msg"] == "token is [REDACTED]"
    assert payload["level"] == "INFO"
    assert payload["component"] == "harness"


def test_extra_fields_redacted() -> None:
    payload = _format(_record("call", component="target", body={"note": SECRET, "token": "t"}))
    assert payload["component"] == "target"
    assert payload["body"] == {"note": "[REDACTED]", "token": "[REDACTED]"}


def test_exception_text_redacted() -> None:
    try:
        raise RuntimeError(f"failed with {SECRET}")
    except RuntimeError:
        rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", (), sys.exc_info())
    payload = _format(rec)
    assert "RuntimeError" in str(payload["exc"])


def test_request_id_from_context() -> None:
    token = request_id_var.set("req_TEST")
    try:
        payload = _format(_record("hello"))
    finally:
        request_id_var.reset(token)
    assert payload["request_id"] == "req_TEST"


def test_uvicorn_color_message_dropped() -> None:
    payload = _format(_record("Started", color_message="\x1b[36mStarted\x1b[0m"))
    assert "color_message" not in payload
