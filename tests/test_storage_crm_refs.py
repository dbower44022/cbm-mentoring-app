"""CRM reference anchors: the REQ-062 boundary after the REQ-086 reconcile (WTK-164)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mentorapp.storage import (
    STRUCTURAL_COLUMN_NAMES,
    AppUser,
    CrmCompanyRef,
    CrmMentorRef,
    SchemaRegistry,
    built_in_fields,
    seed_built_in_registry,
)

_REF_ENTITIES = (CrmCompanyRef, CrmMentorRef)


def test_refs_are_identity_anchors_only() -> None:
    # REQ-062: master data (names, statuses) is never forked app-side — an
    # anchor is its key, the CRM record id, and (for the mentor) the REQ-019
    # app-user pairing, which is a linkage, not master data.
    expected = {
        CrmCompanyRef: {"crmCompanyRefID", "crmCompanyID"},
        CrmMentorRef: {"crmMentorRefID", "crmMentorID", "userID"},
    }
    for entity, columns in expected.items():
        table = inspect(entity).local_table
        own_columns = set(table.columns.keys()) - STRUCTURAL_COLUMN_NAMES
        assert own_columns == columns, table.name


def test_company_anchor_carries_the_crm_id_string(session: Session) -> None:
    ref = CrmCompanyRef(crm_company_id="6867f3e2a1b2c3d4e")
    session.add(ref)
    session.commit()

    fetched = session.scalars(select(CrmCompanyRef)).one()
    assert fetched.crm_company_id == "6867f3e2a1b2c3d4e"
    # The anchor key is app-generated UUIDv7 (REQ-047), distinct from CRM truth.
    assert fetched.crm_company_ref_id.version == 7


@pytest.mark.parametrize(
    ("entity", "crm_id_attr"),
    [
        (CrmCompanyRef, "crm_company_id"),
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


def test_mentor_pairing_is_one_live_anchor_per_app_user(session: Session) -> None:
    # WTK-167/REQ-019: the userID pairing is what confines engagement rows to
    # the signed-in mentor, so two live mentor anchors must never claim the
    # same app user — while unpaired anchors (NULL) coexist freely.
    user = AppUser(crm_user_id="crm-user-1", username="mentor@example.org")
    session.add(user)
    session.flush()
    session.add(CrmMentorRef(crm_mentor_id="m-1", user_id=user.user_id))
    session.add(CrmMentorRef(crm_mentor_id="m-2"))
    session.add(CrmMentorRef(crm_mentor_id="m-3"))
    session.commit()

    session.add(CrmMentorRef(crm_mentor_id="m-4", user_id=user.user_id))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_mentor_pairing_requires_a_real_app_user(session: Session) -> None:
    # The pairing is a hard foreign key: scoping on a userID no appUser owns
    # would silently scope to nobody.
    session.add(CrmMentorRef(crm_mentor_id="m-9", user_id=uuid.uuid4()))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_registry_definitions_derive_from_the_columns() -> None:
    by_name = {spec.field_name: spec for spec in built_in_fields(list(_REF_ENTITIES))}
    assert set(by_name) == {
        "crmCompanyRefID",
        "crmCompanyID",
        "crmMentorRefID",
        "crmMentorID",
        "userID",
    }
    for spec in by_name.values():
        # Re-pointing an anchor changes what every linked app-owned row is
        # about — the CRM id columns are history-tracked (DB-S5).
        if spec.field_type == "text":
            assert spec.required_flag
            assert spec.history_tracked_flag
            assert spec.field_label.startswith("CRM ")
        else:
            assert spec.field_type in {"id", "reference"}
    # The pairing column is DB-R2b's sanctioned re-appearance of the appUser
    # key — same name, same meaning — and re-pairing is history-tracked.
    pairing = by_name["userID"]
    assert pairing.r2b_reappearance
    assert pairing.history_tracked_flag
    assert not pairing.required_flag


def test_seed_registers_every_anchor_field(session: Session) -> None:
    result = seed_built_in_registry(session, list(_REF_ENTITIES))
    assert len(result.inserted) == 5

    rows = session.scalars(
        select(SchemaRegistry).where(
            SchemaRegistry.entity_type.in_([e.__tablename__ for e in _REF_ENTITIES])
        )
    ).all()
    assert len(rows) == 5
