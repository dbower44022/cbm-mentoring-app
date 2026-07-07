"""Mentor access: areas, isolation, leadership span, staff gates (WTK-186).

Drives the WTK-167/176 access design end to end over a real store: the
seeded REQ-071 area sources with their role grants, the REQ-019 injected
row filter (mentor A structurally cannot read mentor B's engagements),
leadership's across-mentors read, and the REQ-084/085 capability gates that
keep resources and events read-only for mentors.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.access.areas import accessible_area_keys
from mentorapp.access.grants import (
    DataSourceAccessError,
    StoredGrantRegistry,
    run_stored_data_source,
)
from mentorapp.access.mentoring import (
    DS_LEADERSHIP_ENGAGEMENTS,
    DS_MENTOR_ENGAGEMENTS,
    DS_MENTOR_RESOURCES,
    DS_MENTOR_SESSIONS,
    LEADERSHIP_ROLE,
    MENTOR_AREAS,
    MENTOR_DATA_SOURCES,
    MENTOR_ROLE,
    authorize_stored_event_management,
    authorize_stored_resource_management,
    seed_mentor_access,
)
from mentorapp.access.views import (
    CAP_EVENT_MANAGE,
    CAP_RESOURCE_MANAGE,
    USER_BASELINE_CAPABILITIES,
    CapabilityError,
)
from mentorapp.storage import (
    AccessGrant,
    AdminSqlError,
    AppUser,
    Client,
    CrmCompanyRef,
    CrmMentorRef,
    DataSource,
    Engagement,
    Event,
    MentoringSession,
    Resource,
    regenerate_read_views,
    seed_built_in_registry,
)
from mentorapp.storage.triage import ENGAGEMENT_TRIAGE_COLUMNS

_MENTOR_ROLES = frozenset({MENTOR_ROLE})
_LEADERSHIP_ROLES = frozenset({LEADERSHIP_ROLE})


def _mentor(session: Session, tag: str) -> AppUser:
    user = AppUser(crm_user_id=f"crm-{tag}", username=f"{tag}@example.org")
    session.add(user)
    session.flush()
    session.add(CrmMentorRef(crm_mentor_id=f"mentor-{tag}", user_id=user.user_id))
    session.flush()
    return user


def _engagement_for(session: Session, user: AppUser, name: str) -> Engagement:
    anchor = session.scalars(
        select(CrmMentorRef).where(CrmMentorRef.user_id == user.user_id)
    ).one()
    company = CrmCompanyRef(crm_company_id=f"acct-{name}")
    session.add(company)
    session.flush()
    client = Client(crm_company_ref_id=company.crm_company_ref_id)
    session.add(client)
    session.flush()
    engagement = Engagement(
        engagement_name=name,
        client_id=client.client_id,
        crm_mentor_ref_id=anchor.crm_mentor_ref_id,
        primary_contact_name=f"Contact {name}",
        primary_contact_email=f"contact@{name}.example",
    )
    session.add(engagement)
    session.flush()
    return engagement


@pytest.fixture()
def store(session: Session) -> tuple[AppUser, AppUser]:
    """A seeded store: areas granted, two mentors with one engagement each."""
    seed_built_in_registry(
        session,
        [
            Client,
            CrmCompanyRef,
            CrmMentorRef,
            Engagement,
            Event,
            MentoringSession,
            Resource,
        ],
    )
    regenerate_read_views(session)
    seed_mentor_access(session)
    mentor_a = _mentor(session, "alice")
    mentor_b = _mentor(session, "bram")
    for user, name in ((mentor_a, "acme"), (mentor_b, "zenith")):
        engagement = _engagement_for(session, user, name)
        session.add(
            MentoringSession(
                engagement_id=engagement.engagement_id,
                scheduled_at=datetime(2026, 6, 1, 15, 0, tzinfo=UTC),
                session_notes=f"<p>Notes for {name}.</p>",
            )
        )
    session.add(
        Resource(
            resource_title="Pricing worksheet",
            resource_location="https://drive.example/pricing.xlsx",
        )
    )
    session.commit()
    return mentor_a, mentor_b


# --- REQ-071: the seven areas ride the one grant boundary ---------------------------


def test_seed_grants_open_all_seven_areas_to_the_mentor_role(
    session: Session, store: tuple[AppUser, AppUser]
) -> None:
    mentor_a, _ = store
    keys = accessible_area_keys(
        MENTOR_AREAS,
        grants=StoredGrantRegistry(session),
        user_id=mentor_a.user_id,
        user_roles=_MENTOR_ROLES,
    )
    assert keys == (
        "contacts",
        "companies",
        "clients",
        "engagements",
        "sessions",
        "resources",
        "events",
    )
    # No roles → no areas: deny by default, same boundary, nothing bespoke.
    assert (
        accessible_area_keys(
            MENTOR_AREAS,
            grants=StoredGrantRegistry(session),
            user_id=mentor_a.user_id,
            user_roles=frozenset(),
        )
        == ()
    )


def test_seed_is_idempotent_and_reconciles_stored_sql(
    session: Session, store: tuple[AppUser, AppUser]
) -> None:
    # Re-running converges: no duplicate sources or grants, and a drifted
    # stored body is reconciled back to the source-controlled spec.
    row = session.scalars(
        select(DataSource).where(
            DataSource.data_source_key == DS_MENTOR_ENGAGEMENTS,
            DataSource.deleted_at.is_(None),
        )
    ).one()
    row.data_source_sql = 'SELECT 1 AS "drifted"'
    session.flush()
    seed_mentor_access(session)
    session.commit()

    rows = session.scalars(select(DataSource).where(DataSource.deleted_at.is_(None))).all()
    assert len(rows) == len(MENTOR_DATA_SOURCES)
    refreshed = {r.data_source_key: r for r in rows}[DS_MENTOR_ENGAGEMENTS]
    assert ":currentUserID" in refreshed.data_source_sql
    assert refreshed.user_row_filter == "userID"


# --- WTK-186: isolation — mentor A cannot see mentor B ------------------------------


def test_mentor_engagement_rows_are_confined_to_the_signed_in_mentor(
    session: Session, store: tuple[AppUser, AppUser]
) -> None:
    mentor_a, mentor_b = store
    rows_a = run_stored_data_source(
        session, DS_MENTOR_ENGAGEMENTS, user_id=mentor_a.user_id, user_roles=_MENTOR_ROLES
    )
    rows_b = run_stored_data_source(
        session, DS_MENTOR_ENGAGEMENTS, user_id=mentor_b.user_id, user_roles=_MENTOR_ROLES
    )
    assert [r["engagementName"] for r in rows_a] == ["acme"]
    assert [r["engagementName"] for r in rows_b] == ["zenith"]
    assert set(ENGAGEMENT_TRIAGE_COLUMNS) <= set(rows_a[0])


def test_the_row_filter_cannot_be_bypassed_by_naming_a_user(
    session: Session, store: tuple[AppUser, AppUser]
) -> None:
    # REQ-019: the scoping is injected server-side from the session user; a
    # caller-supplied currentUserID is rejected, never merged.
    mentor_a, mentor_b = store
    with pytest.raises(AdminSqlError, match="bound server-side"):
        run_stored_data_source(
            session,
            DS_MENTOR_ENGAGEMENTS,
            user_id=mentor_a.user_id,
            user_roles=_MENTOR_ROLES,
            params={"currentUserID": str(mentor_b.user_id)},
        )


def test_mentor_sessions_area_is_scoped_through_its_engagement(
    session: Session, store: tuple[AppUser, AppUser]
) -> None:
    mentor_a, _ = store
    rows = run_stored_data_source(
        session, DS_MENTOR_SESSIONS, user_id=mentor_a.user_id, user_roles=_MENTOR_ROLES
    )
    assert [r["engagementName"] for r in rows] == ["acme"]
    assert rows[0]["sessionNotes"] == "<p>Notes for acme.</p>"


def test_mentors_cannot_run_the_leadership_source(
    session: Session, store: tuple[AppUser, AppUser]
) -> None:
    mentor_a, _ = store
    with pytest.raises(DataSourceAccessError):
        run_stored_data_source(
            session,
            DS_LEADERSHIP_ENGAGEMENTS,
            user_id=mentor_a.user_id,
            user_roles=_MENTOR_ROLES,
        )


def test_leadership_sees_across_mentors(
    session: Session, store: tuple[AppUser, AppUser]
) -> None:
    leader = AppUser(crm_user_id="crm-lead", username="lead@example.org")
    session.add(leader)
    session.commit()
    rows = run_stored_data_source(
        session,
        DS_LEADERSHIP_ENGAGEMENTS,
        user_id=leader.user_id,
        user_roles=_LEADERSHIP_ROLES,
    )
    assert [r["engagementName"] for r in rows] == ["acme", "zenith"]


def test_resources_are_an_org_wide_read_for_mentors(
    session: Session, store: tuple[AppUser, AppUser]
) -> None:
    mentor_a, _ = store
    rows = run_stored_data_source(
        session, DS_MENTOR_RESOURCES, user_id=mentor_a.user_id, user_roles=_MENTOR_ROLES
    )
    assert [r["resourceTitle"] for r in rows] == ["Pricing worksheet"]


# --- REQ-084/REQ-085: staff-maintained, mentors read-only ---------------------------


def test_mentors_hold_no_maintenance_capability(session: Session) -> None:
    # The baseline every signed-in user holds excludes both keys, and with
    # no accessGrant row the stored gates refuse — that is the whole of
    # "read-only to mentors" on the write side.
    assert CAP_RESOURCE_MANAGE not in USER_BASELINE_CAPABILITIES
    assert CAP_EVENT_MANAGE not in USER_BASELINE_CAPABILITIES
    mentor = uuid.uuid4()
    with pytest.raises(CapabilityError):
        authorize_stored_resource_management(session, user_id=mentor)
    with pytest.raises(CapabilityError):
        authorize_stored_event_management(session, user_id=mentor)


def test_granted_staff_maintain_resources_and_events(session: Session) -> None:
    staff = AppUser(crm_user_id="crm-staff", username="staff@example.org")
    session.add(staff)
    session.flush()
    session.add(AccessGrant(user_id=staff.user_id, access_grant_key=CAP_RESOURCE_MANAGE))
    session.add(AccessGrant(user_id=staff.user_id, access_grant_key=CAP_EVENT_MANAGE))
    session.commit()

    authorize_stored_resource_management(session, user_id=staff.user_id)
    authorize_stored_event_management(session, user_id=staff.user_id)
