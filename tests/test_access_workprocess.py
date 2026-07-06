"""Tests for WorkprocessAccess (WTK-096, REQ-041): visibility and launchability
are INHERITED from data-source access — the REQ-006 grant boundary, never a
second permission model — and registering is the admin capability gate."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from mentorapp.access import (
    ADMIN_CAPABILITIES,
    CAP_WORKPROCESS_REGISTER,
    CapabilityError,
    DataSourceAccessError,
    InMemoryCapabilityRegistry,
    WorkprocessNotTargetedError,
    authorize_stored_workprocess_registration,
    authorize_workprocess_launch,
    authorize_workprocess_registration,
    grant_data_source_role,
    revoke_data_source_role,
    visible_workprocesses,
)
from mentorapp.storage import (
    AccessGrant,
    AppUser,
    DataSource,
    WorkprocessRegistration,
    WorkprocessRegistrationDataSource,
    utcnow,
)

MENTOR_ROLES = frozenset({"mentor"})
COORDINATOR_ROLES = frozenset({"coordinator"})


def _user(session: Session, username: str = "mentor.one") -> AppUser:
    user = AppUser(crm_user_id=f"crm-{username}", username=username)
    session.add(user)
    session.flush()
    return user


def _source(session: Session, key: str = "engagementRoster") -> DataSource:
    source = DataSource(data_source_key=key, data_source_name=key, data_source_sql="SELECT 1")
    session.add(source)
    session.flush()
    return source


def _registration(
    session: Session, name: str = "Bulk Reassign Mentor", *, targets: list[DataSource]
) -> WorkprocessRegistration:
    registration = WorkprocessRegistration(
        workprocess_name=name,
        workprocess_description="Reassign selected engagements to another mentor",
        selection_contract="multiple",
        action_classification="modifying",
        step_graph={
            "startStepKey": "confirm",
            "steps": [{"stepKey": "confirm", "nextStepKey": None}],
        },
    )
    session.add(registration)
    session.flush()
    for source in targets:
        session.add(
            WorkprocessRegistrationDataSource(
                workprocess_registration_id=registration.workprocess_registration_id,
                data_source_id=source.data_source_id,
            )
        )
    session.flush()
    return registration


def test_capability_vocabulary_names_the_admin_registration_gate() -> None:
    # REQ-041: registering is the Administrator persona's act — the key is an
    # ADMIN capability. Launching deliberately has NO capability: it stays
    # the inherited data-source boundary.
    assert CAP_WORKPROCESS_REGISTER == "workprocess.register"
    assert CAP_WORKPROCESS_REGISTER in ADMIN_CAPABILITIES


def test_visibility_is_inherited_from_data_source_access(session: Session) -> None:
    source = _source(session)
    registration = _registration(session, targets=[source])
    grant_data_source_role(session, data_source_key="engagementRoster", role_name="mentor")
    session.commit()

    # A granted role sees the source's whole action list; an ungranted one
    # sees nothing — because it does not see the SOURCE (deny by default).
    assert visible_workprocesses(
        session, data_source_key="engagementRoster", user_roles=MENTOR_ROLES
    ) == [registration]
    assert (
        visible_workprocesses(
            session, data_source_key="engagementRoster", user_roles=COORDINATOR_ROLES
        )
        == []
    )


def test_revoking_the_source_grant_removes_the_action_list_with_no_sweep(
    session: Session,
) -> None:
    source = _source(session)
    _registration(session, targets=[source])
    grant_data_source_role(session, data_source_key="engagementRoster", role_name="mentor")
    session.commit()
    assert (
        visible_workprocesses(
            session, data_source_key="engagementRoster", user_roles=MENTOR_ROLES
        )
        != []
    )

    # One act, one boundary (REQ-041/REQ-006): revoking the DATA-SOURCE grant
    # is the only revocation there is — no per-workprocess grant to sweep.
    revoke_data_source_role(session, data_source_key="engagementRoster", role_name="mentor")
    assert (
        visible_workprocesses(
            session, data_source_key="engagementRoster", user_roles=MENTOR_ROLES
        )
        == []
    )


def test_launch_authorizes_at_the_same_boundary(session: Session) -> None:
    source = _source(session)
    registration = _registration(session, targets=[source])
    grant_data_source_role(session, data_source_key="engagementRoster", role_name="mentor")
    session.commit()
    user = _user(session)

    # Covered: passes quietly.
    authorize_workprocess_launch(
        session,
        registration,
        data_source_key="engagementRoster",
        user_id=user.user_id,
        user_roles=MENTOR_ROLES,
    )
    # Uncovered: the standard REQ-006 denial, not a workprocess-specific one.
    with pytest.raises(DataSourceAccessError):
        authorize_workprocess_launch(
            session,
            registration,
            data_source_key="engagementRoster",
            user_id=user.user_id,
            user_roles=COORDINATOR_ROLES,
        )


def test_launch_refuses_a_source_the_registration_does_not_target(
    session: Session,
) -> None:
    targeted = _source(session)
    other = _source(session, "mentorRoster")
    registration = _registration(session, targets=[targeted])
    for key in ("engagementRoster", "mentorRoster"):
        grant_data_source_role(session, data_source_key=key, role_name="mentor")
    session.commit()
    user = _user(session)

    with pytest.raises(WorkprocessNotTargetedError):
        authorize_workprocess_launch(
            session,
            registration,
            data_source_key=other.data_source_key,
            user_id=user.user_id,
            user_roles=MENTOR_ROLES,
        )


def test_untargeting_closes_the_launch_path_live_rows_only(session: Session) -> None:
    source = _source(session)
    registration = _registration(session, targets=[source])
    grant_data_source_role(session, data_source_key="engagementRoster", role_name="mentor")
    session.commit()
    user = _user(session)

    link = registration.data_source_links[0]
    link.deleted_at = utcnow()
    session.flush()

    with pytest.raises(WorkprocessNotTargetedError):
        authorize_workprocess_launch(
            session,
            registration,
            data_source_key="engagementRoster",
            user_id=user.user_id,
            user_roles=MENTOR_ROLES,
        )


def test_registration_gate_requires_the_admin_capability() -> None:
    admin = uuid.uuid4()
    mentor = uuid.uuid4()
    lookup = InMemoryCapabilityRegistry({admin: frozenset({CAP_WORKPROCESS_REGISTER})})

    authorize_workprocess_registration(lookup, user_id=admin)
    with pytest.raises(CapabilityError):
        authorize_workprocess_registration(lookup, user_id=mentor)


def test_stored_registration_gate_reads_live_access_grant_rows(session: Session) -> None:
    admin = _user(session, "admin.one")
    grant = AccessGrant(user_id=admin.user_id, access_grant_key=CAP_WORKPROCESS_REGISTER)
    session.add(grant)
    session.flush()

    authorize_stored_workprocess_registration(session, user_id=admin.user_id)

    # Revocation is a soft delete and changes the very next attempt.
    grant.deleted_at = utcnow()
    session.flush()
    with pytest.raises(CapabilityError):
        authorize_stored_workprocess_registration(session, user_id=admin.user_id)
