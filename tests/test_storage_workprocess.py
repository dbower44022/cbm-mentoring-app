"""Tests for the workprocess data model (WTK-095, REQ-041/REQ-042): entity-named
UUIDv7 keys, the shared action vocabularies in their one canonical home, the
registration ↔ dataSource many-to-many, the run ↔ registration many-to-one,
and the run's pending-until-commit JSON state."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mentorapp.storage import (
    ACTION_CLASSIFICATIONS,
    RUN_STATE_COMMITTED,
    RUN_STATE_DISCARDED,
    RUN_STATE_IN_FLIGHT,
    RUN_STATES,
    SELECTION_CONTRACTS,
    TERMINAL_RUN_STATES,
    AppUser,
    DataSource,
    WorkprocessRegistration,
    WorkprocessRegistrationDataSource,
    WorkprocessRun,
    registrations_for_data_source,
    utcnow,
)


def _user(session: Session, username: str = "mentor.one") -> AppUser:
    user = AppUser(crm_user_id=f"crm-{username}", username=username)
    session.add(user)
    session.flush()
    return user


def _source(session: Session, key: str = "engagementRoster") -> DataSource:
    source = DataSource(
        data_source_key=key,
        data_source_name=key,
        data_source_sql="SELECT 1",
    )
    session.add(source)
    session.flush()
    return source


def _registration(
    session: Session,
    name: str = "Bulk Reassign Mentor",
    *,
    selection_contract: str = "multiple",
    action_classification: str = "modifying",
) -> WorkprocessRegistration:
    registration = WorkprocessRegistration(
        workprocess_name=name,
        workprocess_description="Reassign selected engagements to another mentor",
        selection_contract=selection_contract,
        action_classification=action_classification,
        step_graph={
            "startStepKey": "chooseMentor",
            "steps": [
                {"stepKey": "chooseMentor", "nextStepKey": "confirm"},
                {"stepKey": "confirm", "nextStepKey": None},
            ],
        },
    )
    session.add(registration)
    session.flush()
    return registration


def _target(
    session: Session, registration: WorkprocessRegistration, source: DataSource
) -> WorkprocessRegistrationDataSource:
    link = WorkprocessRegistrationDataSource(
        workprocess_registration_id=registration.workprocess_registration_id,
        data_source_id=source.data_source_id,
    )
    session.add(link)
    session.flush()
    return link


def _run(
    session: Session,
    registration: WorkprocessRegistration,
    source: DataSource,
    user: AppUser,
) -> WorkprocessRun:
    run = WorkprocessRun(
        workprocess_registration_id=registration.workprocess_registration_id,
        data_source_id=source.data_source_id,
        user_id=user.user_id,
        selected_record_ids=["rec-1", "rec-2"],
        current_step_key="chooseMentor",
    )
    session.add(run)
    session.flush()
    return run


def test_vocabularies_match_the_standard() -> None:
    # REQ-041: the grid standard's contracts and classifications, pinned in
    # their ONE canonical home (panel actions validate against these too).
    assert SELECTION_CONTRACTS == ("none", "single", "multiple")
    assert ACTION_CLASSIFICATIONS == ("safe", "modifying", "destructive")
    # REQ-042: one working state, two exits — commit applies, discard retains.
    assert RUN_STATES == ("inFlight", "committed", "discarded")
    assert TERMINAL_RUN_STATES == (RUN_STATE_COMMITTED, RUN_STATE_DISCARDED)
    assert RUN_STATE_IN_FLIGHT not in TERMINAL_RUN_STATES


def test_ui_and_storage_share_one_canonical_action_vocabulary() -> None:
    # The theming-precedent direction (ui imports storage): PanelAction
    # validates against exactly these objects, so a panel action and a
    # workprocess registration can never disagree about the vocabulary.
    from mentorapp.ui import record_preview

    assert record_preview.SELECTION_CONTRACTS is SELECTION_CONTRACTS
    assert record_preview.ACTION_CLASSIFICATIONS is ACTION_CLASSIFICATIONS


def test_registration_round_trips_with_entity_named_uuid7_key(session: Session) -> None:
    registration = _registration(session)
    session.commit()

    assert isinstance(registration.workprocess_registration_id, uuid.UUID)
    # UUIDv7 (REQ-047) — the version nibble is the key policy's fingerprint.
    assert registration.workprocess_registration_id.version == 7
    assert "workprocessRegistrationID" in WorkprocessRegistration.__table__.columns
    assert "id" not in WorkprocessRegistration.__table__.columns
    assert registration.step_graph["startStepKey"] == "chooseMentor"
    assert registration.row_version == 1


def test_registration_rejects_off_vocabulary_values() -> None:
    # DB-S7 backstop at the persistence boundary: the old shell-era "bulk"
    # classification has no words in the action-list grouping and refuses.
    with pytest.raises(ValueError, match="selectionContract"):
        WorkprocessRegistration(
            workprocess_name="Bad contract",
            workprocess_description="x",
            selection_contract="some",
            action_classification="safe",
        )
    with pytest.raises(ValueError, match="actionClassification"):
        WorkprocessRegistration(
            workprocess_name="Bad classification",
            workprocess_description="x",
            selection_contract="none",
            action_classification="bulk",
        )


def test_registration_names_unique_among_live_rows(session: Session) -> None:
    first = _registration(session)
    session.commit()

    # Display names are the action-list identity — two live registrations
    # cannot share one (REQ-041).
    with pytest.raises(IntegrityError):
        _registration(session)
    session.rollback()

    # DB-S3: a soft-deleted corpse never blocks re-registering the name.
    session.add(first)
    first.deleted_at = utcnow()
    session.flush()
    assert _registration(session).workprocess_name == "Bulk Reassign Mentor"


def test_registration_targets_many_sources_and_sources_carry_many(
    session: Session,
) -> None:
    engagements = _source(session, "engagementRoster")
    mentors = _source(session, "mentorRoster")
    reassign = _registration(session)
    export = _registration(session, "Export Roster", selection_contract="none")
    _target(session, reassign, engagements)
    _target(session, reassign, mentors)
    _target(session, export, engagements)
    session.commit()

    # Registration side of the many-to-many.
    assert {link.data_source.data_source_key for link in reassign.data_source_links} == {
        "engagementRoster",
        "mentorRoster",
    }
    # Source side: the action-list read, name-ordered.
    listed = registrations_for_data_source(session, "engagementRoster")
    assert [r.workprocess_name for r in listed] == ["Bulk Reassign Mentor", "Export Roster"]
    assert registrations_for_data_source(session, "mentorRoster") == [reassign]


def test_target_pair_unique_among_live_rows_and_retargetable(session: Session) -> None:
    source = _source(session)
    registration = _registration(session)
    link = _target(session, registration, source)
    session.commit()

    with pytest.raises(IntegrityError):
        _target(session, registration, source)
    session.rollback()

    # Untargeting is a soft delete; the pair can be re-added later.
    session.add_all([source, registration, link])
    link.deleted_at = utcnow()
    session.flush()
    _target(session, registration, source)
    session.commit()


def test_untargeting_removes_the_source_from_the_action_list_read(
    session: Session,
) -> None:
    source = _source(session)
    registration = _registration(session)
    link = _target(session, registration, source)
    session.commit()
    assert registrations_for_data_source(session, "engagementRoster") == [registration]

    link.deleted_at = utcnow()
    session.flush()
    assert registrations_for_data_source(session, "engagementRoster") == []

    # A retired registration disappears too — live-rows-only, end to end.
    revived = _target(session, registration, source)
    registration.deleted_at = utcnow()
    session.flush()
    assert revived.deleted_at is None
    assert registrations_for_data_source(session, "engagementRoster") == []


def test_run_inherits_the_selection_and_starts_in_flight(session: Session) -> None:
    source = _source(session)
    registration = _registration(session)
    run = _run(session, registration, source, _user(session))
    session.commit()

    assert run.workprocess_run_id.version == 7
    assert "workprocessRunID" in WorkprocessRun.__table__.columns
    assert run.run_state == RUN_STATE_IN_FLIGHT
    # REQ-042: the launch inherits the selection — the records AND the source.
    assert run.selected_record_ids == ["rec-1", "rec-2"]
    assert run.data_source_id == source.data_source_id
    assert run.registration is registration
    assert registration.runs == [run]
    # Pending state is empty until steps answer; nothing exists elsewhere.
    assert run.step_answers == {}
    assert run.completed_at is None


def test_run_rejects_an_unknown_state(session: Session) -> None:
    run = _run(session, _registration(session), _source(session), _user(session))
    with pytest.raises(ValueError, match="runState"):
        run.run_state = "paused"


def test_run_requires_live_parent_rows(session: Session) -> None:
    # The FK triple is real: a run of nothing is a defect, not a soft state.
    run = WorkprocessRun(
        workprocess_registration_id=uuid.uuid4(),
        data_source_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )
    session.add(run)
    with pytest.raises(IntegrityError):
        session.flush()


def test_discarded_run_is_retained_evidence_not_a_deletion(session: Session) -> None:
    source = _source(session)
    registration = _registration(session)
    run = _run(session, registration, source, _user(session))
    run.step_answers = {"chooseMentor": {"mentorID": "m-9"}}
    session.commit()

    # Leave = cancel (REQ-042): the state flips, the row and its pending
    # answers survive as evidence — deletedAt stays null; this is not DB-S3
    # soft delete, it is a terminal domain state.
    run.run_state = RUN_STATE_DISCARDED
    run.completed_at = utcnow()
    session.commit()

    assert run.deleted_at is None
    assert run.step_answers == {"chooseMentor": {"mentorID": "m-9"}}
    assert run.run_state in TERMINAL_RUN_STATES
