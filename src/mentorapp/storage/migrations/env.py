"""Alembic environment — one migration chain, dialect-aware (REQ-047..REQ-054).

One chain serves both dialects: models carry per-dialect variants (JSONB on
Postgres, JSON on SQLite) and every partial index declares both
``postgresql_where`` and ``sqlite_where``, so the same revision compiles
correctly on each. SQLite ALTERs run in batch mode (``render_as_batch``);
``Base.metadata``'s naming convention keeps constraint names deterministic
across dialects.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

from mentorapp.storage import Base

config = context.config

# Deploy/test override; the alembic.ini default is the local dev SQLite file.
_url_override = os.environ.get("MENTORAPP_DATABASE_URL")
if _url_override:
    config.set_main_option("sqlalchemy.url", _url_override)

target_metadata = Base.metadata


def _is_sqlite(url_or_name: str) -> bool:
    return url_or_name.startswith("sqlite")


def run_migrations_offline() -> None:
    """Emit migration SQL without a live connection (``alembic upgrade --sql``)."""
    url = config.get_main_option("sqlalchemy.url") or ""
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=_is_sqlite(url),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection.

    The test suite passes its own connection via ``config.attributes`` so
    in-memory SQLite databases migrate in place; standalone runs build an
    engine from the configured URL.
    """
    connection = config.attributes.get("connection")
    if connection is not None:
        _run_with_connection(connection)
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as conn:
        _run_with_connection(conn)
        conn.commit()


def _run_with_connection(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=_is_sqlite(connection.dialect.name),
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
