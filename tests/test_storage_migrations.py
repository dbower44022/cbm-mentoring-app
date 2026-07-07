"""Migration-chain tests (WTK-133): upgrade head builds exactly the model schema.

The migrated database — not ``create_all`` — is what production runs on, so
these tests assert the chain reproduces the ORM metadata (tables, columns,
nullability, index names and uniqueness) and that the partial live-row
unique indexes actually enforce REQ-052 semantics after a real upgrade.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, Table, create_engine, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mentorapp.storage import (
    SHARED_TYPE_SCALE_NAME,
    TYPE_SCALE_DEFAULT_SIZES,
    Base,
    OptionSet,
    schema_drift_findings,
    shared_type_scale,
    utcnow,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_SCRIPT_LOCATION = _REPO_ROOT / "src" / "mentorapp" / "storage" / "migrations"


def _production_tables() -> dict[str, Table]:
    # Other test modules register throwaway entities on the shared Base, so
    # under the full suite Base.metadata holds more than production. The
    # migration chain owns exactly the tables mapped from the mentorapp package.
    return {
        mapper.local_table.name: mapper.local_table
        for mapper in Base.registry.mappers
        if mapper.class_.__module__.startswith("mentorapp.")
        and isinstance(mapper.local_table, Table)
    }


def _alembic_config() -> Config:
    config = Config(_ALEMBIC_INI)
    # Absolute path: alembic.ini's script_location is repo-root-relative and
    # pytest need not run from the repo root.
    config.set_main_option("script_location", str(_SCRIPT_LOCATION))
    return config


def _run_alembic(engine: Engine, direction: str, revision: str) -> None:
    config = _alembic_config()
    with engine.connect() as connection:
        # env.py picks this connection up so the in-memory database migrates
        # in place instead of alembic opening its own engine.
        config.attributes["connection"] = connection
        if direction == "upgrade":
            command.upgrade(config, revision)
        else:
            command.downgrade(config, revision)
        connection.commit()


@pytest.fixture()
def migrated_engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fks(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]

    _run_alembic(engine, "upgrade", "head")
    yield engine
    engine.dispose()


def test_upgrade_head_creates_every_model_table(migrated_engine: Engine) -> None:
    actual = set(inspect(migrated_engine).get_table_names())
    expected = set(_production_tables()) | {"alembic_version"}
    assert actual == expected


def test_migrated_columns_match_models(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    for table in _production_tables().values():
        actual = {c["name"]: c["nullable"] for c in inspector.get_columns(table.name)}
        expected = {c.name: bool(c.nullable) for c in table.columns}
        assert actual == expected, f"column drift on {table.name}"


def test_migrated_indexes_match_models(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    for table in _production_tables().values():
        actual = {i["name"]: bool(i["unique"]) for i in inspector.get_indexes(table.name)}
        expected = {i.name: bool(i.unique) for i in table.indexes}
        assert actual == expected, f"index drift on {table.name}"


def test_partial_unique_index_enforces_live_rows_only(migrated_engine: Engine) -> None:
    # The REQ-052 contract end-to-end on a migrated database: a soft-deleted
    # corpse never blocks a live re-add, but two live duplicates collide.
    # The probe name is fictional on purpose — 0014 seeds real option sets
    # (engagementStatus et al.) whose live rows a probe must not collide with.
    with Session(migrated_engine) as session:
        corpse = OptionSet(option_set_name="probeStatusOptions")
        corpse.deleted_at = utcnow()
        session.add(corpse)
        session.add(OptionSet(option_set_name="probeStatusOptions"))
        session.commit()

        session.add(OptionSet(option_set_name="probeStatusOptions"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_upgrade_head_seeds_the_shared_type_scale(migrated_engine: Engine) -> None:
    # WTK-116/REQ-046: the ONE app-wide scale exists from first boot with the
    # design-default sizes — the row the WTK-114 type-scale surface serves.
    with Session(migrated_engine) as session:
        scale = shared_type_scale(session)
        assert scale.type_scale_name == SHARED_TYPE_SCALE_NAME
        assert scale.scale_steps == TYPE_SCALE_DEFAULT_SIZES
        assert scale.row_version == 1
        assert scale.created_by is None  # seeded, no acting user


def test_upgrade_head_registry_matches_the_schema(migrated_engine: Engine) -> None:
    # REQ-050 end to end: after the whole chain — including 0014's renames,
    # retirements, and reseeds — the registry and the actual schema agree,
    # so the startup drift gate would pass on a freshly migrated database.
    with Session(migrated_engine) as session:
        assert schema_drift_findings(session) == []


def test_downgrade_base_removes_every_table(migrated_engine: Engine) -> None:
    _run_alembic(migrated_engine, "downgrade", "base")
    assert set(inspect(migrated_engine).get_table_names()) == {"alembic_version"}
