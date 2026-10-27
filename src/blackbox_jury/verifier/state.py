"""Pure helpers over state snapshots (gateway ``state_snapshot`` events)."""

from __future__ import annotations

from typing import Any

from blackbox_jury.evidence.schema import State

PRIMARY_KEYS: dict[str, str] = {
    "customers": "customer_id",
    "orders": "order_id",
    "refunds": "refund_id",
    "approvals": "approval_id",
    "outbox": "message_id",
}


def rows(state: State, table: str) -> list[dict[str, Any]]:
    value = state.get(table, [])
    return [r for r in value if isinstance(r, dict)]


def index(state: State, table: str) -> dict[Any, dict[str, Any]]:
    pk = PRIMARY_KEYS[table]
    return {r.get(pk): r for r in rows(state, table)}


def added_rows(before: State, after: State, table: str) -> list[dict[str, Any]]:
    """Rows present after but not before (by primary key), in after-order."""
    before_keys = set(index(before, table))
    pk = PRIMARY_KEYS[table]
    return [r for r in rows(after, table) if r.get(pk) not in before_keys]


def changed_rows(
    before: State, after: State, table: str
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(before, after) pairs for rows whose content changed."""
    old = index(before, table)
    pk = PRIMARY_KEYS[table]
    return [
        (old[r.get(pk)], r) for r in rows(after, table) if r.get(pk) in old and old[r.get(pk)] != r
    ]


def order_owners(state: State) -> dict[str, str]:
    return {
        str(o["order_id"]): str(o["customer_id"])
        for o in rows(state, "orders")
        if "order_id" in o and "customer_id" in o
    }
