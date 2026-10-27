"""Engine/session factory and migrations (SQLite: WAL + foreign keys)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _db_file(url: str) -> Path | None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        raise ValueError("only SQLite is supported in the MVP")
    db_file = Path(parsed.database) if parsed.database and parsed.database != ":memory:" else None
    if db_file is not None:
        # Owner-only: evidence is synthetic, but still not world-readable. Created here so
        # migrations work on a fresh checkout (no data/ directory yet).
        db_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return db_file


def make_engine(url: str) -> Engine:
    db_file = _db_file(url)
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        cur.execute("PRAGMA journal_mode = WAL")
        cur.execute("PRAGMA busy_timeout = 15000")
        cur.close()
        if db_file is not None and db_file.exists():
            for f in (db_file, Path(f"{db_file}-wal"), Path(f"{db_file}-shm")):
                if f.exists():
                    os.chmod(f, 0o600)

    return engine


def upgrade(url: str) -> None:
    """Apply all migrations (``alembic upgrade head``) to ``url``."""
    _db_file(url)
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = make_engine(url)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """One transaction: commit on success, roll back on any exception."""
        session = self._factory()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def ping(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            return True
        except Exception:
            return False
