"""Prefixed, time-sortable, unguessable identifiers (ULID layout, Crockford base32)."""

from __future__ import annotations

import re
import secrets
import threading
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PREFIX = re.compile(r"^[a-z]{2,4}$")


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        chars.append(_ALPHABET[rem])
    return "".join(reversed(chars))


_lock = threading.Lock()
_last_ts = -1
_last_rnd = 0


def new_id(prefix: str) -> str:
    """Return e.g. ``run_01JB8Z...``: 48-bit ms timestamp + 80 random bits.

    Monotonic within a process (ULID spec): ids created in the same millisecond
    increment the random part, so creation order == sort order.
    """
    global _last_ts, _last_rnd
    if not _PREFIX.match(prefix):
        raise ValueError(f"invalid id prefix: {prefix!r}")
    with _lock:
        ts = int(time.time() * 1000) & ((1 << 48) - 1)
        if ts <= _last_ts:
            ts, rnd = _last_ts, _last_rnd + 1
            if rnd >= 1 << 80:  # random part exhausted in this ms: move to the next ms
                ts, rnd = ts + 1, secrets.randbits(79)
        else:
            rnd = secrets.randbits(79)  # top bit clear leaves headroom for increments
        _last_ts, _last_rnd = ts, rnd
    return f"{prefix}_{_encode(ts, 10)}{_encode(rnd, 16)}"
