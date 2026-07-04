"""Tests for the supporting entities (REQ-054, REQ-059, REQ-060, REQ-061, REQ-041)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mentorapp.storage import (
    SELECTION_CONTRACTS,
    DuplicateOverride,
    FieldChange,
    PostalCode,
    UserPreference,
    WorkprocessRegistration,
    utcnow,
    uuid7,
)


def test_selection_contract_vocabulary_matches_the_standard() -> None:
    assert SELECTION_CONTRACTS == ("none", "single", "multiple")


def test_field_change_history_panel_is_one_indexed_ordered_lookup(session: Session) -> None:
    mentor_id = uuid7()
    other_record = uuid7()
    editor = uuid7()
    base_time = utcnow()
    session.add_all(
        [
            FieldChange(
                entity_type="Engagement",
                record_id=mentor_id,
                field_name="engagementStatus",
                old_value=None,
                new_value="active-option-id",
                changed_at=base_time,
                changed_by=editor,
            ),
            FieldChange(
                entity_type="Engagement",
                record_id=mentor_id,
                field_name="engagementCapacity",
                old_value=2,
                new_value=3,
                changed_at=base_time + timedelta(seconds=1),
                changed_by=editor,
            ),
            # Another record's change must not appear in the panel read.
            FieldChange(
                entity_type="Engagement",
                record_id=other_record,
                field_name="engagementCapacity",
                old_value=1,
                new_value=2,
            ),
        ]
    )
    session.commit()

    # The History panel read (REQ-054): one indexed lookup, already ordered.
    panel = session.scalars(
        select(FieldChange)
        .where(FieldChange.entity_type == "Engagement", FieldChange.record_id == mentor_id)
        .order_by(FieldChange.changed_at)
    ).all()
    assert [c.field_name for c in panel] == ["engagementStatus", "engagementCapacity"]
    # JSON old/new values keep their types across the round trip; null means unset.
    assert panel[0].old_value is None
    assert panel[0].new_value == "active-option-id"
    assert panel[1].old_value == 2
    assert panel[1].new_value == 3
    assert all(c.changed_by == editor for c in panel)
    assert "ix_fieldChange_record_live" in {i.name for i in FieldChange.__table__.indexes}


def test_duplicate_override_snapshots_rules_and_candidates(session: Session) -> None:
    created_record = uuid7()
    candidates = [str(uuid7()), str(uuid7())]
    session.add(
        DuplicateOverride(
            entity_type="Person",
            record_id=created_record,
            matched_rule_names=["personByEmail", "personByNamePhone"],
            candidate_record_ids=candidates,
            override_reason="Same name, different person — verified by phone call",
        )
    )
    session.commit()

    loaded = session.scalars(select(DuplicateOverride)).one()
    assert loaded.record_id == created_record
    assert loaded.matched_rule_names == ["personByEmail", "personByNamePhone"]
    assert loaded.candidate_record_ids == candidates
    assert loaded.override_reason is not None
    indexes = {i.name for i in DuplicateOverride.__table__.indexes}
    assert "ix_duplicateOverride_record_live" in indexes


def test_user_preference_org_default_and_user_override_coexist(session: Session) -> None:
    user = uuid7()
    org_default = UserPreference(
        preference_key="grid.mentorRoster.columns", preference_value={"columns": ["mentorName"]}
    )
    user_row = UserPreference(
        user_id=user,
        preference_key="grid.mentorRoster.columns",
        preference_value={"columns": ["mentorName", "engagementStatus"]},
    )
    session.add_all([org_default, user_row])
    session.commit()

    rows = session.scalars(
        select(UserPreference).where(
            UserPreference.preference_key == "grid.mentorRoster.columns"
        )
    ).all()
    assert {r.user_id for r in rows} == {None, user}


def test_user_preference_one_live_row_per_user_and_key(session: Session) -> None:
    user = uuid7()
    session.add(UserPreference(user_id=user, preference_key="nav.pinnedViews"))
    session.commit()

    session.add(UserPreference(user_id=user, preference_key="nav.pinnedViews"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Soft-deleting the row frees the key for that user (partial unique, DB-S3).
    live = session.scalars(select(UserPreference)).one()
    live.deleted_at = utcnow()
    session.commit()
    session.add(UserPreference(user_id=user, preference_key="nav.pinnedViews"))
    session.commit()


def test_user_preference_one_live_org_default_per_key(session: Session) -> None:
    # NULL userID rows never collide in a plain unique index — the dedicated
    # org-default partial index must enforce one live default per key.
    session.add(UserPreference(preference_key="nav.startupView"))
    session.commit()
    session.add(UserPreference(preference_key="nav.startupView"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_postal_code_unique_per_country_and_code_across_live_rows(session: Session) -> None:
    session.add(
        PostalCode(postal_code_value="49503", city_name="Grand Rapids", state_code="MI")
    )
    session.commit()

    session.add(PostalCode(postal_code_value="49503", city_name="Duplicate", state_code="MI"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # A refresh job replaces rows by soft delete + re-add without collision.
    stale = session.scalars(select(PostalCode)).one()
    stale.deleted_at = utcnow()
    session.commit()
    session.add(
        PostalCode(postal_code_value="49503", city_name="Grand Rapids", state_code="MI")
    )
    session.commit()
    assert session.scalars(select(PostalCode)).all()[0].country_code == "US"


def test_workprocess_registration_round_trips_the_contract(session: Session) -> None:
    session.add(
        WorkprocessRegistration(
            workprocess_name="Bulk Reassign Mentor",
            workprocess_description="Reassign selected engagements to another mentor",
            target_data_source_keys=["engagementRoster", "mentorRoster"],
            selection_contract="multiple",
            action_classification="bulk",
        )
    )
    session.commit()

    loaded = session.scalars(select(WorkprocessRegistration)).one()
    assert loaded.selection_contract in SELECTION_CONTRACTS
    assert loaded.target_data_source_keys == ["engagementRoster", "mentorRoster"]

    # Display names are unique across live registrations (action-list identity).
    session.add(
        WorkprocessRegistration(
            workprocess_name="Bulk Reassign Mentor",
            workprocess_description="Duplicate name",
            selection_contract="none",
            action_classification="bulk",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
