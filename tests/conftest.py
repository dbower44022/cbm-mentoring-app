"""Shared fixtures for the storage-model test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mentorapp.storage import Base


@pytest.fixture()
def session() -> Iterator[Session]:
    # The API suite shares this session with TestClient's worker thread: SQLite
    # forbids cross-thread use by default, and the default per-thread pool would
    # hand that thread a fresh empty in-memory database. One static connection,
    # shared — the tests are single-request serial, so this is safe.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite ignores foreign keys unless asked — the tests must exercise them.
    @event.listens_for(engine, "connect")
    def _enable_fks(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]

    Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()
