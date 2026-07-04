"""Option lifecycle over the registry-driven read surface (WTK-144).

REQ-051's acceptance: adding, renaming, and deactivating option values are
data changes — never DDL — and historic records keep rendering through the
generated read views. REQ-050's drift angle here: none of these lifecycle
operations may ever register as schema drift. Startup *failure* on drift and
hidden-from-new-entry enforcement live where they run: the drift check in
test_storage_readsurface, migration-time seeding in test_storage_seed, the
write engine's inactiveOption rejection in test_api_processes.
"""

from __future__ import annotations

import uuid

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from mentorapp.storage import (
    BaseEntity,
    OptionSet,
    OptionValue,
    SchemaRegistry,
    entity_key,
    regenerate_read_views,
    schema_drift_findings,
)


class Program(BaseEntity):
    __tablename__ = "Program"

    program_id: Mapped[uuid.UUID] = entity_key("programID")
    program_name: Mapped[str] = mapped_column("programName", nullable=False)
    # A built-in choice field: the column stores the optionValueID (DB-S7).
    program_stage: Mapped[uuid.UUID | None] = mapped_column("programStage")


def _schema_snapshot(session: Session) -> dict[str, tuple[str, ...]]:
    """Every table's column names — the lifecycle tests assert this never moves."""
    inspector = inspect(session.get_bind())
    return {
        table: tuple(col["name"] for col in inspector.get_columns(table))
        for table in inspector.get_table_names()
    }


def _seed_program(session: Session) -> uuid.UUID:
    """Registry + option set + one historic record; returns the stored value ID."""
    stage_set = OptionSet(option_set_name="programStageOptions")
    planning = OptionValue(
        option_set=stage_set, option_value_name="planning", option_value_label="Planning"
    )
    session.add_all([stage_set, planning])
    session.flush()
    session.add_all(
        [
            SchemaRegistry(
                entity_type="Program",
                field_name="programID",
                field_type="id",
                field_label="Program ID",
            ),
            SchemaRegistry(
                entity_type="Program",
                field_name="programName",
                field_type="text",
                field_label="Name",
            ),
            SchemaRegistry(
                entity_type="Program",
                field_name="programStage",
                field_type="choice",
                field_label="Stage",
                option_set_id=stage_set.option_set_id,
            ),
        ]
    )
    session.add(Program(program_name="Spring 2026", program_stage=planning.option_value_id))
    session.commit()
    regenerate_read_views(session)
    return planning.option_value_id


def _program_rows(session: Session) -> list[dict[str, object]]:
    return [dict(r) for r in session.execute(text('SELECT * FROM "vwProgram"')).mappings()]


def test_adding_a_value_is_pure_data_and_serves_immediately(session: Session) -> None:
    _seed_program(session)
    before = _schema_snapshot(session)

    stage_set = session.scalars(select(OptionSet)).one()
    completed = OptionValue(
        option_set=stage_set, option_value_name="completed", option_value_label="Completed"
    )
    session.add(completed)
    session.flush()  # assigns the UUIDv7 ID the new record stores
    session.add(Program(program_name="Fall 2025", program_stage=completed.option_value_id))
    session.commit()

    # No DDL happened, no drift appeared — the whole change is rows (REQ-051).
    assert _schema_snapshot(session) == before
    assert schema_drift_findings(session) == []
    # The existing view renders the new value with no regeneration: the label
    # join reads optionValue at query time, not at view-generation time.
    labels = {row["programName"]: row["programStageLabel"] for row in _program_rows(session)}
    assert labels == {"Spring 2026": "Planning", "Fall 2025": "Completed"}


def test_renaming_a_label_is_one_row_update_and_zero_record_touches(
    session: Session,
) -> None:
    planning_id = _seed_program(session)
    record = session.scalars(select(Program)).one()
    version_before = record.row_version

    value = session.get(OptionValue, planning_id)
    assert value is not None
    value.option_value_label = "Kickoff"
    session.commit()

    # The historic record re-renders under the new label untouched: it still
    # stores the same optionValueID and its rowVersion never moved (DB-S7).
    (row,) = _program_rows(session)
    assert row["programStageLabel"] == "Kickoff"
    session.refresh(record)
    assert record.program_stage == planning_id
    assert record.row_version == version_before
    assert schema_drift_findings(session) == []


def test_deactivating_a_value_keeps_historic_records_rendering(session: Session) -> None:
    planning_id = _seed_program(session)

    value = session.get(OptionValue, planning_id)
    assert value is not None
    value.active_flag = False
    session.commit()

    # Retirement hides the value from new entry (the write engine's
    # inactiveOption rejection) but the row stays live — never soft-deleted —
    # so the view's label join keeps serving every historic record (REQ-051).
    session.refresh(value)
    assert value.active_flag is False
    assert value.deleted_at is None
    (row,) = _program_rows(session)
    assert row["programStageLabel"] == "Planning"
    assert schema_drift_findings(session) == []
