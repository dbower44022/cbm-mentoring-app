"""Shared fixtures for the storage-model test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from mentorapp.storage import Base


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    # SQLite ignores foreign keys unless asked — the tests must exercise them.
    @event.listens_for(engine, "connect")
    def _enable_fks(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]

    Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()
