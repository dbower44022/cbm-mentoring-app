"""CRM reference anchors: the REQ-062 ownership boundary in schema (WTK-150)."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mentorapp.storage import (
    STRUCTURAL_COLUMN_NAMES,
    CrmClientRef,
    CrmEngagementRef,
    CrmMentorRef,
    SchemaRegistry,
    built_in_fields,
    seed_built_in_registry,
)

_REF_ENTITIES = (CrmClientRef, CrmEngagementRef, CrmMentorRef)


def test_refs_are_identity_anchors_only() -> None:
    # REQ-062: master data (names, statuses, associations) is never forked
    # app-side — an anchor is exactly its key plus the CRM record id.
    expected = {
        CrmClientRef: {"crmClientRefID", "crmClientID"},
        CrmEngagementRef: {"crmEngagementRefID", "crmEngagementID"},
        CrmMentorRef: {"crmMentorRefID", "crmMentorID"},
    }
    for entity, columns in expected.items():
        table = inspect(entity).local_table
        own_columns = set(table.columns.keys()) - STRUCTURAL_COLUMN_NAMES
        assert own_columns == columns, table.name


def test_crm_engagement_ref_carries_the_crm_id_string(session: Session) -> None:
    ref = CrmEngagementRef(crm_engagement_id="6867f3e2a1b2c3d4e")
    session.add(ref)
    session.commit()

    fetched = session.scalars(select(CrmEngagementRef)).one()
    assert fetched.crm_engagement_id == "6867f3e2a1b2c3d4e"
    # The anchor key is app-generated UUIDv7 (REQ-047), distinct from CRM truth.
    assert fetched.crm_engagement_ref_id.version == 7


@pytest.mark.parametrize(
    ("entity", "crm_id_attr"),
    [
        (CrmClientRef, "crm_client_id"),
        (CrmEngagementRef, "crm_engagement_id"),
        (CrmMentorRef, "crm_mentor_id"),
    ],
)
def test_one_live_anchor_per_crm_record(
    session: Session, entity: type, crm_id_attr: str
) -> None:
    session.add(entity(**{crm_id_attr: "abc123"}))
    session.commit()

    session.add(entity(**{crm_id_attr: "abc123"}))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # A soft-deleted anchor is a corpse, not a blocker: re-anchoring the same
    # CRM record must succeed (REQ-052 partial uniqueness).
    live = session.scalars(select(entity)).one()
    live.soft_delete()
    session.commit()
    session.add(entity(**{crm_id_attr: "abc123"}))
    session.commit()


def test_registry_definitions_derive_from_the_columns() -> None:
    by_name = {spec.field_name: spec for spec in built_in_fields(list(_REF_ENTITIES))}
    assert set(by_name) == {
        "crmClientRefID",
        "crmClientID",
        "crmEngagementRefID",
        "crmEngagementID",
        "crmMentorRefID",
        "crmMentorID",
    }
    for spec in by_name.values():
        # Re-pointing an anchor changes what every linked app-owned row is
        # about — the CRM id columns are history-tracked (DB-S5).
        if spec.field_type == "text":
            assert spec.required_flag
            assert spec.history_tracked_flag
            assert spec.field_label.startswith("CRM ")
        else:
            assert spec.field_type == "id"


def test_seed_registers_every_anchor_field(session: Session) -> None:
    result = seed_built_in_registry(session, list(_REF_ENTITIES))
    assert len(result.inserted) == 6

    rows = session.scalars(
        select(SchemaRegistry).where(
            SchemaRegistry.entity_type.in_([e.__tablename__ for e in _REF_ENTITIES])
        )
    ).all()
    assert len(rows) == 6
