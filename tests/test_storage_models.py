"""Tests for the schema-registry and option-list models (REQ-050, REQ-051, REQ-055)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from mentorapp.storage import (
    Base,
    OptionSet,
    OptionValue,
    SchemaRegistry,
    utcnow,
    uuid7,
)

STRUCTURAL_COLUMNS = {
    "createdAt",
    "createdBy",
    "modifiedAt",
    "modifiedBy",
    "deletedAt",
    "deletedBy",
    "rowVersion",
    "customAttributes",
}


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


def test_uuid7_is_version_7_and_time_ordered() -> None:
    first = uuid7()
    time.sleep(0.002)
    second = uuid7()
    assert first.version == 7
    assert first.variant == uuid.RFC_4122
    assert second > first


def test_option_value_belongs_to_option_set(session: Session) -> None:
    status_set = OptionSet(option_set_name="engagementStatus")
    status_set.option_values = [
        OptionValue(option_value_name="active", option_value_label="Active"),
        OptionValue(
            option_value_name="paused", option_value_label="Paused", option_value_sort_order=1
        ),
    ]
    session.add(status_set)
    session.commit()

    loaded = session.scalars(select(OptionSet)).one()
    assert [v.option_value_name for v in loaded.option_values] == ["active", "paused"]
    assert all(v.option_set is loaded for v in loaded.option_values)
    assert all(v.active_flag for v in loaded.option_values)


def test_option_value_requires_existing_set(session: Session) -> None:
    orphan = OptionValue(option_set_id=uuid7(), option_value_name="x", option_value_label="X")
    session.add(orphan)
    with pytest.raises(IntegrityError):
        session.commit()


def test_schema_registry_uses_option_set(session: Session) -> None:
    status_set = OptionSet(option_set_name="engagementStatus")
    field = SchemaRegistry(
        entity_type="Engagement",
        field_name="engagementStatus",
        field_type="choice",
        field_label="Status",
        option_set=status_set,
        history_tracked_flag=True,
        searchable_flag=False,
    )
    plain_field = SchemaRegistry(
        entity_type="Mentor",
        field_name="mentorName",
        field_type="text",
        field_label="Mentor Name",
        searchable_flag=True,
    )
    session.add_all([field, plain_field])
    session.commit()

    loaded = session.scalars(
        select(SchemaRegistry).where(SchemaRegistry.field_name == "engagementStatus")
    ).one()
    assert loaded.option_set is not None
    assert loaded.option_set.option_set_name == "engagementStatus"
    assert not loaded.user_defined_flag
    no_options = session.scalars(
        select(SchemaRegistry).where(SchemaRegistry.field_name == "mentorName")
    ).one()
    assert no_options.option_set is None


def test_field_name_unique_across_live_rows_only(session: Session) -> None:
    def make_row(entity: str) -> SchemaRegistry:
        return SchemaRegistry(
            entity_type=entity, field_name="mentorName", field_type="text", field_label="Name"
        )

    session.add(make_row("Mentor"))
    session.commit()

    # A second live row with the same fieldName — even on another entity — collides:
    # fieldName uniqueness is system-wide (DB-R2).
    session.add(make_row("Engagement"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Soft-deleting the first row frees the name (partial unique index, DB-S3).
    live = session.scalars(select(SchemaRegistry)).one()
    live.deleted_at = utcnow()
    session.commit()
    session.add(make_row("Engagement"))
    session.commit()
    assert len(session.scalars(select(SchemaRegistry)).all()) == 2


def test_row_version_increments_and_detects_staleness(session: Session) -> None:
    status_set = OptionSet(option_set_name="sessionOutcome")
    session.add(status_set)
    session.commit()
    assert status_set.row_version == 1

    status_set.option_set_name = "sessionOutcomes"
    session.commit()
    assert status_set.row_version == 2

    # A writer carrying a stale rowVersion updates zero rows (DB-S4).
    session.execute(
        OptionSet.__table__.update()
        .where(OptionSet.option_set_id == status_set.option_set_id)
        .values(optionSetName="renamed elsewhere", rowVersion=3)
    )
    status_set.option_set_name = "stale write"
    with pytest.raises(StaleDataError):
        session.commit()


def test_every_table_carries_structural_columns_and_no_bare_id() -> None:
    for table in Base.metadata.tables.values():
        column_names = set(table.columns.keys())
        assert column_names >= STRUCTURAL_COLUMNS, table.name
        assert "id" not in column_names, table.name
        # modifiedAt powers the change feed and is indexed on every table (DB-S5).
        indexed = {col.name for idx in table.indexes for col in idx.columns}
        assert "modifiedAt" in indexed, table.name


def test_entity_named_keys_match_referenced_primary_key() -> None:
    fk_pairs = [
        (fk.parent.name, fk.column.name)
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
    ]
    assert fk_pairs, "expected foreign keys in the model"
    # A foreign key carries the identical name as the primary key it references (DB-R2).
    assert all(parent == referenced for parent, referenced in fk_pairs), fk_pairs
