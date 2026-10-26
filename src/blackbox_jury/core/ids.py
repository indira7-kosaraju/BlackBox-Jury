"""Prefixed, time-sortable, unguessable identifiers (ULID layout, Crockford base32)."""

from __future__ import annotations

import re
import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PREFIX = re.compile(r"^[a-z]{2,4}$")


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        chars.append(_ALPHABET[rem])
    return "".join(reversed(chars))


def new_id(prefix: str) -> str:
    """Return e.g. ``run_01JB8Z...``: 48-bit ms timestamp + 80 random bits."""
    if not _PREFIX.match(prefix):
        raise ValueError(f"invalid id prefix: {prefix!r}")
    ts = int(time.time() * 1000) & ((1 << 48) - 1)
    rnd = secrets.randbits(80)
    return f"{prefix}_{_encode(ts, 10)}{_encode(rnd, 16)}"
