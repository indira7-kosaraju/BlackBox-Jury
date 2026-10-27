"""Alembic environment. URL comes from the caller (``db.session.upgrade``) or alembic.ini."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from blackbox_jury.db.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Execute nothing on the connection before begin_transaction(): SQLAlchemy 2.x
        # would autobegin an outer transaction that Alembic never commits, losing the
        # alembic_version row (the next upgrade would then fail).
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()


run_migrations()
