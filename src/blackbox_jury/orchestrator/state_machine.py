"""Run and execution state machines (PRD §10, DATABASE.md §3.5-3.6).

All status changes go through ``check_run`` / ``check_execution``. Terminal
states never move again, and an execution that did not complete can never be
reported as passed (the DB CHECKs enforce the same rule as a backstop).
"""

from __future__ import annotations

from typing import Literal

RunStatus = Literal[
    "queued", "running", "verifying", "reviewing", "completed", "failed", "cancelled", "timed_out"
]
ExecutionStatus = Literal[
    "pending", "running", "collected", "verified", "reviewed", "error", "cancelled", "timed_out"
]

RUN_TERMINAL: frozenset[str] = frozenset({"completed", "failed", "cancelled", "timed_out"})
RUN_ACTIVE: frozenset[str] = frozenset({"queued", "running", "verifying", "reviewing"})
EXECUTION_TERMINAL: frozenset[str] = frozenset(
    {"verified", "reviewed", "error", "cancelled", "timed_out"}
)

_RUN: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "cancelled", "failed"}),
    "running": frozenset(
        {"verifying", "reviewing", "completed", "cancelled", "failed", "timed_out"}
    ),
    "verifying": frozenset({"reviewing", "completed", "cancelled", "failed", "timed_out"}),
    "reviewing": frozenset({"completed", "cancelled", "failed", "timed_out"}),
}
_EXECUTION: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "cancelled", "timed_out", "error"}),
    "running": frozenset({"collected", "error", "cancelled", "timed_out"}),
    "collected": frozenset({"verified", "error"}),
    "verified": frozenset({"reviewed"}),
}


class IllegalTransitionError(ValueError):
    pass


def check_run(old: str, new: str) -> None:
    if new not in _RUN.get(old, frozenset()):
        raise IllegalTransitionError(f"run: {old} -> {new} is not allowed")


def check_execution(old: str, new: str) -> None:
    if new not in _EXECUTION.get(old, frozenset()):
        raise IllegalTransitionError(f"execution: {old} -> {new} is not allowed")
