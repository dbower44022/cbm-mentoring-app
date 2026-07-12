"""The session details read surface over the wire (REQ-110, PI-015).

Covers the one-read detail payload (identity, engagement/client references,
derived attendees, transcript state triple), the on-demand transcript read
(text only when attached; asking while absent is a state answer, not an
error), the DEC-098 participation derivation (invited until completed,
attended after), and scoping (another mentor's session answers the uniform
404; leadership spans mentors). The transcript text staying OUT of the
detail payload is pinned — that separation is the requirement's performance
rule, not an accident.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from identity_stub import header_user_id
from mentorapp.access.mentoring import LEADERSHIP_ROLE, MENTOR_ROLE
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.routers.workprocess import get_role_source
from mentorapp.main import create_app
from mentorapp.storage import (
    AppUser,
    Client,
    CrmCompanyRef,
    CrmMentorRef,
    Engagement,
    Event,
    MentoringSession,
    OptionValue,
    Partner,
    Resource,
    seed_built_in_registry,
)

_WHEN = datetime(2026, 7, 9, 18, 0, tzinfo=UTC)


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
    seed_built_in_registry(
        session,
        [
            Client,
            CrmCompanyRef,
            CrmMentorRef,
            Engagement,
            Event,
            MentoringSession,
            Partner,
            Resource,
        ],
    )
    session.commit()
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user_id] = header_user_id
    app.dependency_overrides[get_role_source] = lambda: roles
    return TestClient(app)


def _headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-ID": str(user_id)}


def _mentor(session: Session, roles: _StubRoles, tag: str) -> tuple[uuid.UUID, CrmMentorRef]:
    user = AppUser(crm_user_id=f"crm-{tag}", username=f"{tag}@example.org")
    session.add(user)
    session.flush()
    anchor = CrmMentorRef(crm_mentor_id=f"mentor-{tag}", user_id=user.user_id)
    session.add(anchor)
    session.flush()
    roles.roles[user.user_id] = frozenset({MENTOR_ROLE})
    return user.user_id, anchor


def _status_id(session: Session, name: str) -> uuid.UUID:
    return session.scalars(
        select(OptionValue.option_value_id).where(OptionValue.option_value_name == name)
    ).one()


def _engagement(session: Session, name: str, mentor: CrmMentorRef | None) -> Engagement:
    company = CrmCompanyRef(crm_company_id=f"acct-{name}")
    session.add(company)
    session.flush()
    client = Client(crm_company_ref_id=company.crm_company_ref_id)
    session.add(client)
    session.flush()
    engagement = Engagement(
        engagement_name=name,
        engagement_status=_status_id(session, "active"),
        client_id=client.client_id,
        crm_mentor_ref_id=mentor.crm_mentor_ref_id if mentor is not None else None,
        primary_contact_name="Sam Contact",
        primary_contact_email="sam@acme.example",
        primary_contact_crm_id="crm-contact-1",
    )
    session.add(engagement)
    session.flush()
    return engagement


def _session_row(
    session: Session,
    engagement: Engagement,
    *,
    status: str = "scheduled",
    transcript: str | None = None,
    meeting_id: str | None = None,
) -> MentoringSession:
    row = MentoringSession(
        engagement_id=engagement.engagement_id,
        scheduled_at=_WHEN,
        session_status=_status_id(session, status),
        session_notes="<p>Notes</p>",
        action_items="<ul><li>Item</li></ul>",
        conference_link="https://meet.example/abc",
        external_meeting_id=meeting_id,
        transcript_text=transcript,
        transcript_source="platform" if transcript else None,
    )
    session.add(row)
    session.flush()
    return row


def test_detail_serves_the_one_read_without_transcript_text(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "frank")
    engagement = _engagement(session, "Alpha", anchor)
    row = _session_row(session, engagement, transcript="one two three four", meeting_id="mtg-1")
    session.commit()

    body = app_client.get(
        f"/sessions/{row.session_id}/detail", headers=_headers(user_id)
    ).json()
    data = body["data"]

    assert data["session"]["sessionID"] == str(row.session_id)
    assert data["engagement"]["engagementName"] == "Alpha"
    assert data["client"]["crmCompanyID"] == "acct-Alpha"
    # The performance rule: the longest text never rides the detail read.
    assert "transcriptText" not in str(data["session"])
    assert data["transcript"] == {
        "state": "attached",
        "source": "platform",
        "wordCount": 4,
    }
    assert body["meta"]["attendeeCount"] == 2


def test_detail_derives_attendees_mentor_and_primary_contact(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "frank")
    engagement = _engagement(session, "Alpha", anchor)
    row = _session_row(session, engagement)
    session.commit()

    attendees = app_client.get(
        f"/sessions/{row.session_id}/detail", headers=_headers(user_id)
    ).json()["data"]["attendees"]

    mentor_row, client_row = attendees
    assert mentor_row["role"] == "mentor"
    assert mentor_row["name"] == "frank@example.org"
    assert client_row["role"] == "client"
    assert client_row["name"] == "Sam Contact"
    assert client_row["email"] == "sam@acme.example"
    assert client_row["companyName"] == "acct-Alpha"
    assert client_row["companyRefID"] is not None
    # The dev detail seam fills phone deterministically — same id, same digits.
    assert client_row["phone"] is not None and client_row["phone"].startswith("(216) 555-")


def test_participation_derives_from_session_status(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "frank")
    engagement = _engagement(session, "Alpha", anchor)
    scheduled = _session_row(session, engagement, status="scheduled")
    completed = _session_row(session, engagement, status="completed")
    session.commit()

    def participations(row: MentoringSession) -> set[str]:
        attendees = app_client.get(
            f"/sessions/{row.session_id}/detail", headers=_headers(user_id)
        ).json()["data"]["attendees"]
        return {entry["participation"] for entry in attendees}

    assert participations(scheduled) == {"invited"}
    assert participations(completed) == {"attended"}


def test_transcript_states_expected_and_unavailable(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "frank")
    engagement = _engagement(session, "Alpha", anchor)
    expected = _session_row(session, engagement, meeting_id="mtg-1")
    pasted = _session_row(session, engagement)
    session.commit()

    def state(row: MentoringSession) -> str:
        return app_client.get(
            f"/sessions/{row.session_id}/detail", headers=_headers(user_id)
        ).json()["data"]["transcript"]["state"]

    assert state(expected) == "expected"
    assert state(pasted) == "unavailable"


def test_transcript_read_serves_text_only_when_attached(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "frank")
    engagement = _engagement(session, "Alpha", anchor)
    attached = _session_row(session, engagement, transcript="alpha beta gamma")
    absent = _session_row(session, engagement)
    session.commit()

    got = app_client.get(
        f"/sessions/{attached.session_id}/transcript", headers=_headers(user_id)
    ).json()
    assert got["data"]["state"] == "attached"
    assert got["data"]["transcriptText"] == "alpha beta gamma"
    assert got["meta"]["wordCount"] == 3

    # Asking while absent is a state answer, never an error.
    got = app_client.get(
        f"/sessions/{absent.session_id}/transcript", headers=_headers(user_id)
    ).json()
    assert got["errors"] is None
    assert got["data"] == {
        "state": "unavailable",
        "transcriptText": None,
        "transcriptSource": None,
    }


def test_scoping_uniform_404_and_leadership_span(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    owner_id, anchor = _mentor(session, roles, "frank")
    other_id, _ = _mentor(session, roles, "gina")
    lead = AppUser(crm_user_id="crm-lead", username="janet@example.org")
    session.add(lead)
    session.flush()
    roles.roles[lead.user_id] = frozenset({LEADERSHIP_ROLE})
    engagement = _engagement(session, "Alpha", anchor)
    row = _session_row(session, engagement)
    session.commit()

    for path in (
        f"/sessions/{row.session_id}/detail",
        f"/sessions/{row.session_id}/transcript",
    ):
        assert app_client.get(path, headers=_headers(owner_id)).status_code == 200
        assert app_client.get(path, headers=_headers(lead.user_id)).status_code == 200
        stranger = app_client.get(path, headers=_headers(other_id))
        missing = app_client.get(
            path.replace(str(row.session_id), str(uuid.uuid4())),
            headers=_headers(owner_id),
        )
        assert stranger.status_code == missing.status_code == 404
