"""Synthetic order/refund store backed by an in-memory SQLite database.

The store lives only inside the gateway container (no files are written).
Money is stored as integer cents. Ids for new rows are deterministic
(R-0001, M-0001, ...) so repeated executions produce identical state.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

SNAPSHOT_TABLES: tuple[tuple[str, str], ...] = (
    ("customers", "customer_id"),
    ("orders", "order_id"),
    ("refunds", "refund_id"),
    ("approvals", "approval_id"),
    ("outbox", "message_id"),
)

_SCHEMA = """
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL,
    phone TEXT NOT NULL, card_last4 TEXT NOT NULL
);
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    item TEXT NOT NULL, total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
    status TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE refunds (
    refund_id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(order_id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0), reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL
);
CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(order_id),
    max_cents INTEGER NOT NULL CHECK (max_cents > 0)
);
CREATE TABLE outbox (
    message_id TEXT PRIMARY KEY, to_addr TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL
);
"""

_SEED_COLUMNS: dict[str, tuple[str, ...]] = {
    "customers": ("customer_id", "name", "email", "phone", "card_last4"),
    "orders": ("order_id", "customer_id", "item", "total_cents", "status", "note"),
    "refunds": ("refund_id", "order_id", "amount_cents", "reason", "status"),
    "approvals": ("approval_id", "order_id", "max_cents"),
    "outbox": ("message_id", "to_addr", "subject", "body"),
}

FAULT_KINDS = ("fail", "fail_after_commit")


class SeedError(ValueError):
    """The seed state is malformed."""


@dataclass
class Fault:
    """Deterministic fault injection for a tool call (from the seed)."""

    tool: str
    match: dict[str, Any]
    kind: str
    remaining: int

    def applies(self, tool: str, args: dict[str, Any]) -> bool:
        return (
            self.remaining > 0
            and tool == self.tool
            and all(args.get(k) == v for k, v in self.match.items())
        )


class Store:
    def __init__(self, seed: dict[str, Any]) -> None:
        if not isinstance(seed, dict):
            raise SeedError("seed must be a JSON object")
        # check_same_thread=False: the gateway serves with a single-threaded HTTPServer,
        # so access is serialised; this only allows serving from a non-creating thread.
        self._db = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)
        self._counters = {"refund": 0, "message": 0}
        self.faults: list[Fault] = []
        try:
            self._load(seed)
        except (sqlite3.Error, KeyError, TypeError) as exc:
            raise SeedError(f"invalid seed: {exc}") from exc

    # -- seeding ---------------------------------------------------------------

    def _load(self, seed: dict[str, Any]) -> None:
        for table, columns in _SEED_COLUMNS.items():
            rows = seed.get(table, [])
            if not isinstance(rows, list):
                raise SeedError(f"seed.{table} must be a list")
            for row in rows:
                if not isinstance(row, dict) or set(row) - set(columns):
                    raise SeedError(f"seed.{table} row has unexpected shape")
                present = [c for c in columns if c in row]
                placeholders = ",".join("?" for _ in present)
                # Table/column names come only from the fixed _SEED_COLUMNS allowlist;
                # all values are bound parameters.
                sql = f"INSERT INTO {table} ({','.join(present)}) VALUES ({placeholders})"  # noqa: S608
                self._db.execute(sql, [row[c] for c in present])
        self._counters["refund"] = self._count("refunds")
        self._counters["message"] = self._count("outbox")
        faults = seed.get("faults", [])
        if not isinstance(faults, list):
            raise SeedError("seed.faults must be a list")
        for f in faults:
            if (
                not isinstance(f, dict)
                or f.get("kind") not in FAULT_KINDS
                or not isinstance(f.get("tool"), str)
                or not isinstance(f.get("match", {}), dict)
                or not isinstance(f.get("times", 1), int)
            ):
                raise SeedError("seed.faults entry is malformed")
            self.faults.append(
                Fault(
                    tool=f["tool"],
                    match=f.get("match", {}),
                    kind=f["kind"],
                    remaining=f.get("times", 1),
                )
            )

    def _count(self, table: str) -> int:
        row = self._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608  # fixed name
        return int(row[0])

    # -- queries ---------------------------------------------------------------

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if row is None:
            return None
        order = dict(row)
        order["refunded_cents"] = self.refunded_cents(order_id)
        approval = self._db.execute(
            "SELECT MAX(max_cents) FROM approvals WHERE order_id = ?", (order_id,)
        ).fetchone()[0]
        order["refund_approval_cents"] = approval
        return order

    def list_orders(self, customer_id: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT order_id, item, total_cents, status FROM orders WHERE customer_id = ? "
            "ORDER BY order_id",
            (customer_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_customer(self, customer_id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        return dict(row) if row else None

    def refunded_cents(self, order_id: str) -> int:
        row = self._db.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM refunds WHERE order_id = ?", (order_id,)
        ).fetchone()
        return int(row[0])

    # -- mutations -------------------------------------------------------------

    def add_refund(self, order_id: str, amount_cents: int, reason: str) -> dict[str, Any]:
        self._counters["refund"] += 1
        refund = {
            "refund_id": f"R-{self._counters['refund']:04d}",
            "order_id": order_id,
            "amount_cents": amount_cents,
            "reason": reason,
            "status": "issued",
        }
        self._db.execute(
            "INSERT INTO refunds (refund_id, order_id, amount_cents, reason, status) "
            "VALUES (:refund_id, :order_id, :amount_cents, :reason, :status)",
            refund,
        )
        return refund

    def add_email(self, to_addr: str, subject: str, body: str) -> dict[str, Any]:
        self._counters["message"] += 1
        msg = {
            "message_id": f"M-{self._counters['message']:04d}",
            "to_addr": to_addr,
            "subject": subject,
            "body": body,
        }
        self._db.execute(
            "INSERT INTO outbox (message_id, to_addr, subject, body) "
            "VALUES (:message_id, :to_addr, :subject, :body)",
            msg,
        )
        return msg

    def set_note(self, order_id: str, note: str) -> bool:
        cur = self._db.execute("UPDATE orders SET note = ? WHERE order_id = ?", (note, order_id))
        return cur.rowcount == 1

    # -- snapshots -------------------------------------------------------------

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Full state, every table ordered by primary key (deterministic)."""
        return {
            table: [
                dict(r)
                # Names come only from the fixed SNAPSHOT_TABLES constant.
                for r in self._db.execute(f"SELECT * FROM {table} ORDER BY {pk}")  # noqa: S608
            ]
            for table, pk in SNAPSHOT_TABLES
        }
