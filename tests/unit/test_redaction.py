from __future__ import annotations

import pytest

from blackbox_jury.core.redaction import REDACTED, Redactor

SECRET = "my-configured-secret-value-123"


@pytest.fixture
def r() -> Redactor:
    return Redactor([SECRET])


def test_configured_literal_masked(r: Redactor) -> None:
    text, count = r.redact_text_with_count(f"a {SECRET} b {SECRET}")
    assert SECRET not in text
    assert count >= 2


@pytest.mark.parametrize(
    "raw",
    [
        "Authorization: Bearer abcdefghijklmnop123",
        "key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV",
        "aws AKIAABCDEFGHIJKLMNOP",
        "gh token ghp_abcdefghijklmnopqrstuvwxyz0123",
        'json {"api_key": "zzzzzzzzzz"}',
        "url ?token=qqqqqqqqqq&x=1",
        "password=hunter2hunter2",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAK\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_credential_shapes_masked(r: Redactor, raw: str) -> None:
    out = r.redact_text(raw)
    assert REDACTED in out
    for fragment in (
        "abcdefghijklmnop123",
        "ABCDEFGHIJKLMNOPQRSTUV",
        "AKIAABCDEFGHIJKLMNOP",
        "zzzzzzzzzz",
        "qqqqqqqqqq",
        "hunter2hunter2",
        "MIIBOgIBAAJBAK",
        "abcdefghijklmnopqrstuvwxyz0123",
    ):
        assert fragment not in out


def test_nested_structures_and_sensitive_keys(r: Redactor) -> None:
    data = {
        "order_id": "A-100",
        "api_key": "anything",
        "headers": {"Authorization": "x"},
        "items": [f"note {SECRET}", 3, None],
        "db_password": "p",
    }
    out = r.redact(data)
    assert out["order_id"] == "A-100"
    assert out["api_key"] == REDACTED
    assert out["headers"]["Authorization"] == REDACTED
    assert out["db_password"] == REDACTED
    assert out["items"] == [f"note {REDACTED}", 3, None]


def test_benign_text_untouched(r: Redactor) -> None:
    text = "Refund order A-100 for $40.00 to customer C-1001 (tokenizer ok)."
    assert r.redact_text_with_count(text) == (text, 0)


def test_short_literals_ignored() -> None:
    # Very short "secrets" would mangle ordinary text; they are not literal-matched.
    assert Redactor(["abc"]).redact_text("abcdef") == "abcdef"
