from __future__ import annotations

import re

import pytest

from blackbox_jury.core.ids import new_id


def test_format_and_uniqueness() -> None:
    ids = {new_id("run") for _ in range(1000)}
    assert len(ids) == 1000
    assert all(re.fullmatch(r"run_[0-9A-HJKMNP-TV-Z]{26}", i) for i in ids)


def test_time_sortable() -> None:
    a = new_id("exe")
    b = new_id("exe")
    assert a[:14] <= b[:14]  # timestamp component is non-decreasing


@pytest.mark.parametrize("bad", ["", "RUN", "r", "toolong", "ru-n"])
def test_rejects_bad_prefix(bad: str) -> None:
    with pytest.raises(ValueError):
        new_id(bad)


def test_monotonic_within_process() -> None:
    ids = [new_id("exe") for _ in range(2000)]
    assert ids == sorted(ids) and len(set(ids)) == 2000
