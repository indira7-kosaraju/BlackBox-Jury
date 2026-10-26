"""Shared secret redaction for logs, stored evidence, API errors and LLM inputs.

One redactor is used everywhere (ARCHITECTURE.md §10). It masks:
- configured secret values (exact match), e.g. the API token;
- common credential shapes (bearer tokens, provider/cloud keys, private keys);
- values of sensitive keys in ``key=value`` / JSON text and in nested mappings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"(?i)^(?:.*[_-])?(?:api[_-]?key|token|secret|password|passwd|authorization|"
    r"credential|private[_-]?key|access[_-]?key)$"
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        REDACTED,
    ),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {REDACTED}"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), REDACTED),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), REDACTED),
    (
        re.compile(
            r"""(?ix)
            ( ["']? (?:[a-z0-9]+[_-])?
              (?:api[_-]?key|token|secret|password|passwd|authorization|access[_-]?key)
              ["']? \s* [:=] \s* ["']? )
            ( [^"'\s,;}&]{4,} )
            """
        ),
        rf"\1{REDACTED}",
    ),
)

_MIN_LITERAL_LENGTH = 8


class Redactor:
    """Masks secrets in text and nested JSON-like structures."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        literals = {s for s in secrets if s and len(s) >= _MIN_LITERAL_LENGTH}
        # Longest first so a secret containing another secret is masked whole.
        self._literals = sorted(literals, key=len, reverse=True)

    def redact_text_with_count(self, text: str) -> tuple[str, int]:
        """Return ``(redacted_text, number_of_masked_spans)``."""
        count = 0
        for literal in self._literals:
            occurrences = text.count(literal)
            if occurrences:
                text = text.replace(literal, REDACTED)
                count += occurrences
        for pattern, replacement in _PATTERNS:
            text, n = pattern.subn(replacement, text)
            count += n
        return text, count

    def redact_text(self, text: str) -> str:
        return self.redact_text_with_count(text)[0]

    def redact(self, value: Any) -> Any:
        """Recursively redact strings; mask values of sensitive keys entirely."""
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, Mapping):
            return {
                k: (
                    REDACTED
                    if isinstance(k, str) and _SENSITIVE_KEY.match(k) and v is not None
                    else self.redact(v)
                )
                for k, v in value.items()
            }
        if isinstance(value, list | tuple):
            return [self.redact(v) for v in value]
        return value
