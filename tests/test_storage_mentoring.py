"""Application-owned mentoring entities and the REQ-063 ownership declaration (WTK-156)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mentorapp.storage import (
    OWNERSHIP_SIDES,
    BaseEntity,
    CrmClientRef,
    CrmEngagementRef,
    CrmMentorRef,
    MeetingNote,
    NextStep,
    ProgressGoal,
    SchemaRegistry,
    SessionLog,
    built_in_fields,
    seed_built_in_registry,
)

_MENTORING_ENTITIES = (MeetingNote, NextStep, ProgressGoal, SessionLog)

# The four narrative (entityType, fieldName) pairs REQ-090 retypes (WTK-205).
_NARRATIVE_FIELDS = (
    ("meetingNote", "meetingNoteBody"),
    ("nextStep", "nextStepDescription"),
    ("progressGoal", "progressGoalDescription"),
    ("sessionLog", "sessionLogSummary"),
)


def _session_log(anchor: CrmEngagementRef) -> SessionLog:
    return SessionLog(
        crm_engagement_ref_id=anchor.crm_engagement_ref_id,
        session_log_date=datetime(2026, 7, 1, 15, 0, tzinfo=UTC),
        session_log_summary="Discussed cash-flow forecast; agreed next check-in.",
    )


def test_every_entity_declares_its_ownership_side() -> None:
    # REQ-063: the ownership side of every data set is declared at design
    # time. Mentoring-process entities are application-owned; the REQ-062
    # anchors declare the CRM side.
    for entity in _MENTORING_ENTITIES:
        assert entity.__ownership_side__ == "application"
    for entity in (CrmClientRef, CrmEngagementRef, CrmMentorRef):
        assert entity.__ownership_side__ == "crm"
    for mapper in BaseEntity.registry.mappers:
        if issubclass(mapper.class_, BaseEntity):
            assert mapper.class_.__ownership_side__ in OWNERSHIP_SIDES


def test_ownership_side_defaults_to_application() -> None:
    # REQ-063: "application" is BaseEntity's declared default side — an entity
    # that states nothing owns its data app-side. This is the default the
    # sessionLog declaration restates; only the REQ-062 CRM anchors must
    # override it. Abstract so the probe never maps a table into the registry.
    class DefaultOwned(BaseEntity):
        __abstract__ = True

    assert DefaultOwned.__ownership_side__ == "application"
    assert SessionLog.__ownership_side__ == "application"


def test_invalid_ownership_side_declaration_is_rejected_at_class_definition() -> None:
    # Enforcement is at class definition — an undeclared side can never map a
    # table, so it can never reach a migration.
    with pytest.raises(TypeError, match="ownership side"):

        class Unowned(BaseEntity):
            __tablename__ = "unownedEntity"
            __ownership_side__ = "somewhere"


def test_session_log_persists_linked_to_its_engagement_anchor(session: Session) -> None:
    # The REQ-063 acceptance shape: a logged mentoring session persists in the
    # application store linked to its engagement's CRM record.
    anchor = CrmEngagementRef(crm_engagement_id="6867f3e2a1b2c3d4e")
    session.add(anchor)
    session.flush()
    session.add(_session_log(anchor))
    session.commit()

    fetched = session.scalars(select(SessionLog)).one()
    assert fetched.session_log_id.version == 7
    assert fetched.crm_engagement_ref.crm_engagement_id == "6867f3e2a1b2c3d4e"


def test_session_log_requires_a_real_engagement_anchor(session: Session) -> None:
    # The many-to-one association is a hard foreign key: an unanchored session
    # log is exactly the recordless tracking REQ-063 exists to end.
    orphan = SessionLog(
        crm_engagement_ref_id=uuid.uuid4(),
        session_log_date=datetime(2026, 7, 1, 15, 0, tzinfo=UTC),
        session_log_summary="orphan",
    )
    session.add(orphan)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_registry_definitions_include_the_r2b_key_reappearance() -> None:
    specs = built_in_fields(list(_MENTORING_ENTITIES))
    by_entity_field = {(spec.entity_type, spec.field_name): spec for spec in specs}

    fk = by_entity_field[("sessionLog", "crmEngagementRefID")]
    assert fk.field_type == "reference"
    assert fk.r2b_reappearance
    assert fk.required_flag
    assert fk.history_tracked_flag

    for narrative in ("meetingNoteBody", "sessionLogSummary"):
        entity = "meetingNote" if narrative == "meetingNoteBody" else "sessionLog"
        assert by_entity_field[(entity, narrative)].searchable_flag


def test_narrative_columns_adopt_the_rich_text_registry_type() -> None:
    # REQ-090 (WTK-205, the WTK-204 delta design): the registry type — never
    # a UI-side list of field names — is what routes a narrative column to
    # the one rich-text control; the retype leaves searchability intact.
    specs = built_in_fields(list(_MENTORING_ENTITIES))
    by_entity_field = {(spec.entity_type, spec.field_name): spec for spec in specs}
    for key in _NARRATIVE_FIELDS:
        assert by_entity_field[key].field_type == "richText"
        assert by_entity_field[key].searchable_flag


def test_reseed_retypes_a_stale_text_narrative_row(session: Session) -> None:
    # Migration 0009's mechanism: rows the 0007/0008 seeds registered as
    # "text" reconcile to the source-controlled richText declaration on the
    # next seed — an update in place, never a duplicate row.
    seed_built_in_registry(session, [MeetingNote])
    session.commit()
    row = session.scalars(
        select(SchemaRegistry).where(
            SchemaRegistry.entity_type == "meetingNote",
            SchemaRegistry.field_name == "meetingNoteBody",
            SchemaRegistry.deleted_at.is_(None),
        )
    ).one()
    row.field_type = "text"
    session.commit()

    seed_built_in_registry(session, [MeetingNote])
    session.commit()
    rows = session.scalars(
        select(SchemaRegistry).where(
            SchemaRegistry.entity_type == "meetingNote",
            SchemaRegistry.field_name == "meetingNoteBody",
            SchemaRegistry.deleted_at.is_(None),
        )
    ).all()
    assert [record.field_type for record in rows] == ["richText"]


def test_seed_registers_both_appearances_of_the_shared_key_name(session: Session) -> None:
    # DB-R2b: the FK carries the identical name as the anchor PK it references,
    # so crmEngagementRefID legitimately has one registry row per entity.
    seed_built_in_registry(session, [CrmEngagementRef, *_MENTORING_ENTITIES])
    session.commit()

    rows = session.scalars(
        select(SchemaRegistry).where(
            SchemaRegistry.field_name == "crmEngagementRefID",
            SchemaRegistry.deleted_at.is_(None),
        )
    ).all()
    assert {(row.entity_type, row.field_type) for row in rows} == {
        ("crmEngagementRef", "id"),
        ("sessionLog", "reference"),
    }
