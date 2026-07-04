"""Read-surface design gate: generated views, drift check, partial-index rule (WTK-131)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import (
    Column,
    DateTime,
    Index,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    select,
    text,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from mentorapp.storage import (
    Base,
    BaseEntity,
    OptionSet,
    OptionValue,
    SchemaDriftError,
    SchemaRegistry,
    entity_key,
    partial_index_rule_violations,
    read_view_name,
    regenerate_read_views,
    run_schema_drift_startup_check,
    schema_drift_findings,
)


class Engagement(BaseEntity):
    __tablename__ = "Engagement"

    engagement_id: Mapped[uuid.UUID] = entity_key("engagementID")
    engagement_name: Mapped[str] = mapped_column("engagementName", nullable=False)
    # A built-in choice field: the column stores the optionValueID (DB-S7).
    engagement_status: Mapped[uuid.UUID | None] = mapped_column("engagementStatus")


def _register_engagement(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed option sets and registry rows; returns (active, gold) option IDs."""
    status_set = OptionSet(option_set_name="engagementStatusOptions")
    active = OptionValue(
        option_set=status_set, option_value_name="active", option_value_label="Active"
    )
    tier_set = OptionSet(option_set_name="engagementTierOptions")
    gold = OptionValue(option_set=tier_set, option_value_name="gold", option_value_label="Gold")
    session.add_all([status_set, active, tier_set, gold])
    session.flush()
    session.add_all(
        [
            SchemaRegistry(
                entity_type="Engagement",
                field_name="engagementID",
                field_type="id",
                field_label="Engagement ID",
            ),
            SchemaRegistry(
                entity_type="Engagement",
                field_name="engagementName",
                field_type="text",
                field_label="Name",
            ),
            SchemaRegistry(
                entity_type="Engagement",
                field_name="engagementStatus",
                field_type="choice",
                field_label="Status",
                option_set_id=status_set.option_set_id,
            ),
            # Admin-created custom attributes: one plain, one choice.
            SchemaRegistry(
                entity_type="Engagement",
                field_name="engagementScore",
                field_type="number",
                field_label="Score",
                user_defined_flag=True,
            ),
            SchemaRegistry(
                entity_type="Engagement",
                field_name="engagementTier",
                field_type="choice",
                field_label="Tier",
                option_set_id=tier_set.option_set_id,
                user_defined_flag=True,
            ),
        ]
    )
    session.commit()
    return active.option_value_id, gold.option_value_id


def test_generated_view_joins_labels_promotes_customs_hides_deleted(
    session: Session,
) -> None:
    active_id, gold_id = _register_engagement(session)
    live = Engagement(
        engagement_name="Mentoring Pilot",
        engagement_status=active_id,
        # User-defined values live in the JSONB bag; choice values are the
        # canonical dashed uuid string, as the API writes them.
        custom_attributes={"engagementScore": 7, "engagementTier": str(gold_id)},
    )
    corpse = Engagement(engagement_name="Old Cohort")
    corpse.soft_delete()
    session.add_all([live, corpse])
    session.commit()

    views = regenerate_read_views(session)
    assert views == [read_view_name("Engagement")]

    rows = session.execute(text('SELECT * FROM "vwEngagement"')).mappings().all()
    # The soft-deleted row is invisible on the read surface (REQ-052).
    assert len(rows) == 1
    row = rows[0]
    assert row["engagementName"] == "Mentoring Pilot"
    # Option labels are already joined in — built-in and user-defined choice.
    assert row["engagementStatusLabel"] == "Active"
    assert row["engagementTierLabel"] == "Gold"
    # Registered custom attributes are promoted to named columns.
    assert row["engagementScore"] == 7


def test_view_regenerates_on_registry_change(session: Session) -> None:
    _register_engagement(session)
    session.add(Engagement(engagement_name="Pilot"))
    session.commit()
    regenerate_read_views(session)

    # The custom-attribute lifecycle: new registry row, then regenerate.
    session.add(
        SchemaRegistry(
            entity_type="Engagement",
            field_name="engagementNotes",
            field_type="text",
            field_label="Notes",
            user_defined_flag=True,
        )
    )
    session.commit()
    regenerate_read_views(session)

    row = session.execute(text('SELECT * FROM "vwEngagement"')).mappings().one()
    assert "engagementNotes" in row


def test_drift_check_passes_when_registry_matches_schema(session: Session) -> None:
    _register_engagement(session)
    assert schema_drift_findings(session) == []
    run_schema_drift_startup_check(session)


def test_drift_check_fails_startup_on_disagreement(session: Session) -> None:
    _register_engagement(session)
    # A built-in row whose column was never migrated in.
    session.add(
        SchemaRegistry(
            entity_type="Engagement",
            field_name="engagementBudget",
            field_type="number",
            field_label="Budget",
        )
    )
    # A registered entity with no table at all.
    session.add(
        SchemaRegistry(
            entity_type="Ghost",
            field_name="ghostID",
            field_type="id",
            field_label="Ghost ID",
        )
    )
    session.commit()

    problems = {
        (f.entity_type, f.field_name, f.problem) for f in schema_drift_findings(session)
    }
    assert ("Engagement", "engagementBudget", "missingColumn") in problems
    assert ("Ghost", None, "missingTable") in problems
    with pytest.raises(SchemaDriftError):
        run_schema_drift_startup_check(session)


def test_drift_check_reports_unregistered_columns(session: Session) -> None:
    _register_engagement(session)
    name_row = session.scalars(
        select(SchemaRegistry).where(SchemaRegistry.field_name == "engagementName")
    ).one()
    name_row.soft_delete()
    session.commit()

    problems = {(f.field_name, f.problem) for f in schema_drift_findings(session)}
    assert ("engagementName", "unregisteredColumn") in problems


def test_partial_index_rule_holds_across_all_tables() -> None:
    # The REQ-052 enforcement: no table in the whole metadata may carry a
    # non-partial index or a UniqueConstraint once it has a deletedAt column.
    assert partial_index_rule_violations(Base.metadata) == []


def test_partial_index_rule_flags_violations() -> None:
    metadata = MetaData()
    live = text('"deletedAt" IS NULL')
    Table(
        "Offender",
        metadata,
        Column("offenderID", String, primary_key=True),
        Column("offenderEmail", String),
        Column("deletedAt", DateTime),
        Column("modifiedAt", DateTime),
        Index("ix_Offender_offenderEmail", "offenderEmail"),
        Index("ix_Offender_modifiedAt", "modifiedAt"),
        Index(
            "uq_Offender_offenderEmail_live",
            "offenderEmail",
            unique=True,
            sqlite_where=live,
            postgresql_where=live,
        ),
        UniqueConstraint("offenderEmail", name="uq_Offender_never_partial"),
    )
    violations = partial_index_rule_violations(metadata)
    assert "Offender.ix_Offender_offenderEmail: no live-row predicate" in violations
    assert (
        "Offender.uq_Offender_never_partial: UniqueConstraint cannot be partial" in violations
    )
    # The change-feed exemption (DB-S10) and properly partial indexes pass.
    assert not any(
        "modifiedAt" in v or "uq_Offender_offenderEmail_live" in v for v in violations
    )
