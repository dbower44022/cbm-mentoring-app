"""Built-in registry seeding gate: derivation, reconciliation, drift (WTK-134)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Column, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from mentorapp.storage import (
    BaseEntity,
    OptionSet,
    OptionValue,
    RegistrySeedError,
    RegistrySeedResult,
    SchemaRegistry,
    built_in_field_from_column,
    built_in_fields,
    entity_key,
    schema_drift_findings,
    seed_built_in_registry,
)


class Mentor(BaseEntity):
    __tablename__ = "Mentor"

    mentor_id: Mapped[uuid.UUID] = entity_key("mentorID")
    mentor_name: Mapped[str] = mapped_column(
        "mentorName",
        String(200),
        nullable=False,
        info={"registry": {"searchableFlag": True}},
    )
    mentor_email: Mapped[str | None] = mapped_column("mentorEmail", String(320))
    # A built-in choice field: the column stores the optionValueID (DB-S7);
    # set and seed values are declared with the column, in the same change-set.
    mentor_status: Mapped[uuid.UUID | None] = mapped_column(
        "mentorStatus",
        info={
            "registry": {
                "fieldLabel": "Status",
                "historyTrackedFlag": True,
                "optionSet": "mentorStatusOptions",
                "optionValues": [("active", "Active"), ("inactive", "Inactive")],
            }
        },
    )
    mentor_capacity: Mapped[int] = mapped_column("mentorCapacity", nullable=False, default=0)


def _mentor_rows(session: Session) -> dict[str, SchemaRegistry]:
    rows = session.scalars(
        select(SchemaRegistry).where(
            SchemaRegistry.entity_type == "Mentor", SchemaRegistry.deleted_at.is_(None)
        )
    ).all()
    return {row.field_name: row for row in rows}


def test_definitions_derive_from_columns_and_info() -> None:
    by_name = {spec.field_name: spec for spec in built_in_fields([Mentor])}
    assert set(by_name) == {
        "mentorID",
        "mentorName",
        "mentorEmail",
        "mentorStatus",
        "mentorCapacity",
    }
    assert by_name["mentorID"].field_type == "id"
    assert by_name["mentorName"].field_type == "text"
    assert by_name["mentorName"].field_label == "Mentor Name"
    assert by_name["mentorName"].required_flag  # non-nullable, no default
    assert by_name["mentorName"].searchable_flag
    assert by_name["mentorEmail"].required_flag is False
    assert by_name["mentorCapacity"].field_type == "number"
    assert by_name["mentorCapacity"].required_flag is False  # defaulted column
    status = by_name["mentorStatus"]
    assert status.field_type == "choice"  # derived from the optionSet declaration
    assert status.field_label == "Status"
    assert status.history_tracked_flag
    assert status.option_set_name == "mentorStatusOptions"


def test_discovery_covers_every_base_entity_subclass() -> None:
    assert any(spec.entity_type == "Mentor" for spec in built_in_fields())


def test_unknown_info_key_is_rejected() -> None:
    bad = Column("mentorGhost", String(10), info={"registry": {"serchableFlag": True}})
    with pytest.raises(RegistrySeedError, match="serchableFlag"):
        built_in_field_from_column("Mentor", bad)


def test_option_values_require_a_set() -> None:
    bad = Column("mentorGhost", String(10), info={"registry": {"optionValues": [("a", "A")]}})
    with pytest.raises(RegistrySeedError, match="optionValues"):
        built_in_field_from_column("Mentor", bad)


def test_seed_inserts_rows_wires_option_sets_and_satisfies_drift_check(
    session: Session,
) -> None:
    result = seed_built_in_registry(session, [Mentor])
    assert len(result.inserted) == 5
    assert result.updated == () and result.retired == ()

    rows = _mentor_rows(session)
    option_set = session.scalars(
        select(OptionSet).where(OptionSet.option_set_name == "mentorStatusOptions")
    ).one()
    assert rows["mentorStatus"].option_set_id == option_set.option_set_id
    values = session.scalars(
        select(OptionValue).where(OptionValue.option_set_id == option_set.option_set_id)
    ).all()
    assert {(v.option_value_name, v.option_value_label) for v in values} == {
        ("active", "Active"),
        ("inactive", "Inactive"),
    }
    # The seeded registry and the actual schema agree — startup would pass.
    assert schema_drift_findings(session) == []


def test_seed_is_idempotent(session: Session) -> None:
    seed_built_in_registry(session, [Mentor])
    session.commit()
    first = {name: row.row_version for name, row in _mentor_rows(session).items()}

    again = seed_built_in_registry(session, [Mentor])
    session.commit()
    assert again == RegistrySeedResult(inserted=(), updated=(), retired=())
    assert {name: row.row_version for name, row in _mentor_rows(session).items()} == first


def test_seed_restores_drifted_metadata(session: Session) -> None:
    seed_built_in_registry(session, [Mentor])
    row = _mentor_rows(session)["mentorName"]
    row.field_label = "Wrong Label"
    row.searchable_flag = False
    session.commit()

    result = seed_built_in_registry(session, [Mentor])
    assert result.updated == ("mentorName",)
    refreshed = _mentor_rows(session)["mentorName"]
    assert refreshed.field_label == "Mentor Name"
    assert refreshed.searchable_flag


def test_seed_retires_rows_for_removed_columns(session: Session) -> None:
    seed_built_in_registry(session, [Mentor])
    # A built-in row whose column a later change-set dropped from the model.
    session.add(
        SchemaRegistry(
            entity_type="Mentor",
            field_name="mentorLegacyScore",
            field_type="number",
            field_label="Legacy Score",
        )
    )
    session.commit()

    result = seed_built_in_registry(session, [Mentor])
    assert result.retired == ("mentorLegacyScore",)
    assert "mentorLegacyScore" not in _mentor_rows(session)
    corpse = session.scalars(
        select(SchemaRegistry).where(SchemaRegistry.field_name == "mentorLegacyScore")
    ).one()
    assert corpse.deleted_at is not None  # soft-retired, never physically deleted


def test_seed_leaves_other_entities_and_user_defined_rows_alone(session: Session) -> None:
    # A live row for an entity outside this sweep, and an admin-created custom
    # attribute on Mentor: neither belongs to the seed.
    session.add_all(
        [
            SchemaRegistry(
                entity_type="Engagement",
                field_name="engagementGoal",
                field_type="text",
                field_label="Goal",
            ),
            SchemaRegistry(
                entity_type="Mentor",
                field_name="mentorNickname",
                field_type="text",
                field_label="Nickname",
                user_defined_flag=True,
            ),
        ]
    )
    session.commit()

    result = seed_built_in_registry(session, [Mentor])
    assert result.retired == ()
    live_names = set(
        session.scalars(
            select(SchemaRegistry.field_name).where(SchemaRegistry.deleted_at.is_(None))
        )
    )
    assert {"engagementGoal", "mentorNickname"} <= live_names


def test_seed_rejects_collision_with_live_user_defined_field(session: Session) -> None:
    # DB-R2: fieldName is unique system-wide; an admin already claimed this
    # name, so the migration must resolve it — never silently overwrite.
    session.add(
        SchemaRegistry(
            entity_type="Mentor",
            field_name="mentorEmail",
            field_type="text",
            field_label="Email",
            user_defined_flag=True,
        )
    )
    session.commit()
    with pytest.raises(RegistrySeedError, match="mentorEmail"):
        seed_built_in_registry(session, [Mentor])


def test_reseeding_preserves_admin_edits_to_option_values(session: Session) -> None:
    seed_built_in_registry(session, [Mentor])
    session.commit()
    value = session.scalars(
        select(OptionValue).where(OptionValue.option_value_name == "active")
    ).one()
    value.option_value_label = "Engaged"  # admin relabel — data, not schema (DB-S7)
    session.commit()

    seed_built_in_registry(session, [Mentor])
    session.commit()
    values = session.scalars(
        select(OptionValue).where(OptionValue.option_value_name == "active")
    ).all()
    assert len(values) == 1
    assert values[0].option_value_label == "Engaged"
