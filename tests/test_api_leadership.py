"""The leadership reporting surface (WTK-171/184, WTK-189/190).

Pins the three dashboard blocks against seeded fixtures — the numbers must
equal what the live rows say (counts by status, sessions held per month,
per-mentor activity), a cancelled (soft-deleted) session must leave them
immediately, and the read must sit behind the one grant boundary: mentors
get the standard 403 envelope; only holders of the leadership engagement
span read the aggregates.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.access.mentoring import LEADERSHIP_ROLE, MENTOR_ROLE, seed_mentor_access
from mentorapp.api.deps import get_session
from mentorapp.api.routers.workprocess import get_role_source
from mentorapp.main import create_app
from mentorapp.storage import (
    AppUser,
    Client,
    CrmCompanyRef,
    CrmMentorRef,
    Engagement,
    MentoringSession,
    OptionValue,
    regenerate_read_views,
    seed_built_in_registry,
)

_HELD_JAN = datetime(2026, 1, 10, 15, 0, tzinfo=UTC)
_HELD_JAN_2 = datetime(2026, 1, 24, 15, 0, tzinfo=UTC)
_HELD_MAR = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
_FUTURE = datetime(2030, 9, 1, 15, 0, tzinfo=UTC)


class _StubRoles:
    """Session roles the way the wired role source will serve them."""

    def __init__(self) -> None:
        self.roles: dict[uuid.UUID, frozenset[str]] = {}

    def user_roles(self, user_id: uuid.UUID) -> frozenset[str]:
        return self.roles.get(user_id, frozenset())


@pytest.fixture()
def roles() -> _StubRoles:
    return _StubRoles()


@pytest.fixture()
def app_client(session: Session, roles: _StubRoles) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_role_source] = lambda: roles
    return TestClient(app)


def _headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-ID": str(user_id)}


def _user(session: Session, roles: _StubRoles, tag: str, role: str) -> AppUser:
    user = AppUser(crm_user_id=f"crm-{tag}", username=f"{tag}@example.org")
    session.add(user)
    session.flush()
    roles.roles[user.user_id] = frozenset({role})
    return user


def _mentor(session: Session, roles: _StubRoles, tag: str) -> CrmMentorRef:
    user = _user(session, roles, tag, MENTOR_ROLE)
    anchor = CrmMentorRef(crm_mentor_id=f"mentor-{tag}", user_id=user.user_id)
    session.add(anchor)
    session.flush()
    return anchor


def _status_id(session: Session, name: str) -> uuid.UUID:
    return session.scalars(
        select(OptionValue.option_value_id).where(OptionValue.option_value_name == name)
    ).one()


def _engagement(
    session: Session, name: str, mentor: CrmMentorRef | None, status_name: str
) -> Engagement:
    company = CrmCompanyRef(crm_company_id=f"acct-{name}")
    session.add(company)
    session.flush()
    client = Client(crm_company_ref_id=company.crm_company_ref_id)
    session.add(client)
    session.flush()
    engagement = Engagement(
        engagement_name=name,
        engagement_status=_status_id(session, status_name),
        client_id=client.client_id,
        crm_mentor_ref_id=mentor.crm_mentor_ref_id if mentor is not None else None,
    )
    session.add(engagement)
    session.flush()
    return engagement


def _session_at(session: Session, engagement: Engagement, at: datetime) -> MentoringSession:
    row = MentoringSession(engagement_id=engagement.engagement_id, scheduled_at=at)
    session.add(row)
    session.flush()
    return row


@pytest.fixture()
def seeded(
    session: Session, roles: _StubRoles
) -> tuple[uuid.UUID, uuid.UUID, MentoringSession]:
    """Two mentors, four engagements, four live sessions + one cancelled.

    Returns (leadership user id, mentor A user id, the cancelled session's
    live sibling is implicit in the counts) — the fixture the accuracy
    assertions read their expected numbers from.
    """
    seed_built_in_registry(
        session, [Client, CrmCompanyRef, CrmMentorRef, Engagement, MentoringSession]
    )
    regenerate_read_views(session)
    seed_mentor_access(session)
    lead = _user(session, roles, "lead", LEADERSHIP_ROLE)
    mentor_a = _mentor(session, roles, "alice")
    mentor_b = _mentor(session, roles, "bram")

    acme = _engagement(session, "Acme Growth", mentor_a, "active")
    zenith = _engagement(session, "Zenith Scaleup", mentor_a, "onHold")
    orbit = _engagement(session, "Orbit Launch", mentor_b, "active")
    # Leadership sees engagements no mentor holds yet (Pending Acceptance).
    _engagement(session, "Unassigned One", None, "pendingAcceptance")

    _session_at(session, acme, _HELD_JAN)
    _session_at(session, acme, _HELD_JAN_2)
    _session_at(session, zenith, _HELD_MAR)
    _session_at(session, orbit, _HELD_MAR)
    _session_at(session, orbit, _FUTURE)  # scheduled, not yet held
    cancelled = _session_at(session, acme, _HELD_MAR)
    cancelled.deleted_at = datetime(2026, 3, 3, tzinfo=UTC)
    session.commit()
    mentor_a_user = session.get(CrmMentorRef, mentor_a.crm_mentor_ref_id)
    assert mentor_a_user is not None and mentor_a_user.user_id is not None
    return lead.user_id, mentor_a_user.user_id, cancelled


def test_reports_derive_from_live_rows(
    app_client: TestClient, seeded: tuple[uuid.UUID, uuid.UUID, MentoringSession]
) -> None:
    lead_id, _, _ = seeded
    body = app_client.get("/leadership/reports", headers=_headers(lead_id)).json()
    data = body["data"]

    # Counts by status: 2 Active, 1 On Hold, 1 Pending Acceptance — largest
    # bucket first; the unassigned engagement counts (the leadership span).
    assert data["engagementsByStatus"][0] == {
        "engagementStatusLabel": "Active",
        "engagementCount": 2,
    }
    labels = {
        row["engagementStatusLabel"]: row["engagementCount"]
        for row in data["engagementsByStatus"]
    }
    assert labels == {"Active": 2, "On Hold": 1, "Pending Acceptance": 1}

    # Sessions held per month: the cancelled March session left the numbers,
    # and the 2030 session hasn't been held.
    assert data["sessionsHeldByMonth"] == [
        {"period": "2026-01", "sessionsHeld": 2},
        {"period": "2026-03", "sessionsHeld": 2},
    ]
    assert body["meta"]["totalSessionsHeld"] == 4
    assert body["meta"]["totalEngagements"] == 4

    # Per-mentor activity: alice holds 3 sessions across 2 engagements; bram
    # held 1 with 1 more scheduled (upcoming sessions don't count as held).
    by_mentor = {row["crmMentorID"]: row for row in data["mentorActivity"]}
    assert by_mentor["mentor-alice"]["engagementCount"] == 2
    assert by_mentor["mentor-alice"]["sessionsHeld"] == 3
    assert by_mentor["mentor-bram"]["engagementCount"] == 1
    assert by_mentor["mentor-bram"]["sessionsHeld"] == 1
    assert by_mentor["mentor-bram"]["lastSessionAt"] is not None


def test_cancelling_a_session_updates_the_numbers_immediately(
    app_client: TestClient,
    session: Session,
    seeded: tuple[uuid.UUID, uuid.UUID, MentoringSession],
) -> None:
    lead_id, _, _ = seeded
    # Cancel (soft-delete) one January session: the very next read reflects it.
    row = session.scalars(
        select(MentoringSession)
        .where(MentoringSession.scheduled_at == _HELD_JAN)
        .where(MentoringSession.deleted_at.is_(None))
    ).one()
    row.deleted_at = datetime(2026, 2, 1, tzinfo=UTC)
    session.commit()

    data = app_client.get("/leadership/reports", headers=_headers(lead_id)).json()["data"]
    assert {"period": "2026-01", "sessionsHeld": 1} in data["sessionsHeldByMonth"]


def test_reports_are_leadership_only(
    app_client: TestClient, seeded: tuple[uuid.UUID, uuid.UUID, MentoringSession]
) -> None:
    _, mentor_user_id, _ = seeded
    # A mentor holds every area grant EXCEPT the leadership span — the same
    # 403 envelope every ungranted data source answers with (REQ-006).
    response = app_client.get("/leadership/reports", headers=_headers(mentor_user_id))
    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "dataSourceAccessDenied"

    # No roles at all: same refusal — deny by default.
    stranger = uuid.uuid4()
    assert app_client.get("/leadership/reports", headers=_headers(stranger)).status_code == 403
