"""The mentor-facing engagement surfaces over the wire (WTK-187, PI-010).

Covers WTK-183's rollup aggregation (newest-first, cancelled sessions leave
immediately) and lifecycle processes (every REQ-075 transition, educate
refusals for invalid ones, DEC-071's decline-is-status-only), the WTK-168/177
session writes over the one write engine, and the WTK-169/178/179 templated
email flows (staff template list, merge, preview-before-send through the
transport seam, share-a-resource carrying the link). Scoping is pinned
throughout: another mentor's engagement answers the same 404 as one that
never existed; leadership spans mentors.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from identity_stub import header_user_id
from mentorapp.access.mentoring import (
    DS_LEADERSHIP_ENGAGEMENTS,
    DS_MENTOR_ENGAGEMENTS,
    LEADERSHIP_ROLE,
    MENTOR_ROLE,
)
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.routers.mentoring import (
    CODE_INVALID_LIFECYCLE_TRANSITION,
    CODE_MISSING_MERGE_FIELDS,
    CODE_NO_APP_CREATED_MEETING,
    CODE_NO_CONTACT_EMAIL,
    CODE_TRANSCRIPT_APPEND_ONLY,
    CODE_TRANSCRIPT_NOT_READY,
    CODE_UNKNOWN_EMAIL_TEMPLATE,
    CODE_UNKNOWN_LIFECYCLE_TRANSITION,
    CODE_UNKNOWN_SESSION_STATUS,
    get_conferencing_provider,
    get_email_transport,
    get_transcript_source,
    transcript_retrieval_job,
)
from mentorapp.api.routers.workprocess import get_role_source
from mentorapp.automation.email_outbound import (
    STAFF_EMAIL_TEMPLATES,
    LoggedEmailTransport,
)
from mentorapp.automation.transcripts import (
    TRANSCRIPT_RETRIEVAL_DELAY,
    TRANSCRIPT_RETRIEVAL_JOB_TYPE,
    FakeSummaryDrafter,
    FakeTranscriptSource,
)
from mentorapp.automation.worker import enqueue_job, process_next_job
from mentorapp.main import create_app
from mentorapp.storage import (
    AppUser,
    BackgroundJob,
    Client,
    CrmCompanyRef,
    CrmMentorRef,
    Engagement,
    Event,
    FieldChange,
    MentoringSession,
    OptionValue,
    Partner,
    Resource,
    as_utc,
    seed_built_in_registry,
    utcnow,
)

_PAST = datetime(2026, 1, 10, 15, 0, tzinfo=UTC)
_LATER_PAST = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
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
def transport() -> LoggedEmailTransport:
    return LoggedEmailTransport()


@pytest.fixture()
def app_client(
    session: Session, roles: _StubRoles, transport: LoggedEmailTransport
) -> TestClient:
    # The registry seed is what production migration 0014 ran: it creates the
    # option sets the lifecycle vocabulary and session writes resolve against.
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
    # The D9 identity seam resolves sessions in production; these are not
    # session-lifecycle tests, so the stub names the acting user directly.
    app.dependency_overrides[get_current_user_id] = header_user_id
    app.dependency_overrides[get_role_source] = lambda: roles
    app.dependency_overrides[get_email_transport] = lambda: transport
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


def _leadership(session: Session, roles: _StubRoles) -> uuid.UUID:
    user = AppUser(crm_user_id="crm-lead", username="lead@example.org")
    session.add(user)
    session.flush()
    roles.roles[user.user_id] = frozenset({LEADERSHIP_ROLE})
    return user.user_id


def _status_id(session: Session, name: str) -> uuid.UUID:
    return session.scalars(
        select(OptionValue.option_value_id).where(OptionValue.option_value_name == name)
    ).one()


def _engagement(
    session: Session,
    name: str,
    mentor: CrmMentorRef | None,
    *,
    status_name: str | None = "active",
    contact_email: str | None = "sam@acme.example",
) -> Engagement:
    company = CrmCompanyRef(crm_company_id=f"acct-{name}")
    session.add(company)
    session.flush()
    client = Client(
        crm_company_ref_id=company.crm_company_ref_id,
        client_program="Core Mentoring",
        client_stage="Growth",
    )
    session.add(client)
    session.flush()
    engagement = Engagement(
        engagement_name=name,
        engagement_status=_status_id(session, status_name) if status_name else None,
        client_id=client.client_id,
        crm_mentor_ref_id=mentor.crm_mentor_ref_id if mentor is not None else None,
        engagement_summary="<p>Summary</p>",
        primary_contact_name="Sam Contact",
        primary_contact_email=contact_email,
        primary_contact_crm_id="crm-contact-1",
    )
    session.add(engagement)
    session.flush()
    return engagement


def _session_row(
    session: Session,
    engagement: Engagement,
    at: datetime,
    *,
    notes: str | None = None,
    action_items: str | None = None,
    link: str | None = None,
) -> MentoringSession:
    row = MentoringSession(
        engagement_id=engagement.engagement_id,
        scheduled_at=at,
        session_status=_status_id(session, "scheduled"),
        session_notes=notes,
        action_items=action_items,
        conference_link=link,
    )
    session.add(row)
    session.flush()
    return row


# --- The rollup read (WTK-183, REQ-073/074/081) ---------------------------------------


def test_rollup_aggregates_notes_newest_first(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "casey")
    engagement = _engagement(session, "Acme Growth", anchor)
    _session_row(session, engagement, _PAST, notes="<p>First session notes</p>")
    _session_row(session, engagement, _LATER_PAST, action_items="<ul><li>Do X</li></ul>")
    _session_row(session, engagement, _FUTURE, link="https://meet.example/1")
    session.commit()

    body = app_client.get(
        f"/engagements/{engagement.engagement_id}/rollup", headers=_headers(user_id)
    ).json()
    assert body["errors"] is None
    rollup = body["data"]["rollup"]
    # Only the two sessions carrying notes/action items, newest first.
    assert [entry["actionItems"] is not None for entry in rollup] == [True, False]
    assert rollup[0]["actionItems"] == "<ul><li>Do X</li></ul>"
    assert rollup[1]["sessionNotes"] == "<p>First session notes</p>"
    # The full session history includes the future one, same order.
    all_sessions = body["data"]["sessions"]
    assert len(all_sessions) == 3
    assert all_sessions[0]["conferenceLink"] == "https://meet.example/1"
    stats = body["data"]["stats"]
    assert stats["totalSessions"] == 3
    assert stats["heldSessions"] == 2
    assert stats["nextSessionAt"] is not None
    assert body["meta"]["rollupCount"] == 2


def test_rollup_serves_references_and_status_label(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "drew")
    engagement = _engagement(session, "Beta Co", anchor)
    session.commit()

    data = app_client.get(
        f"/engagements/{engagement.engagement_id}/rollup", headers=_headers(user_id)
    ).json()["data"]
    assert data["engagement"]["engagementStatus"] == "active"
    assert data["engagement"]["engagementStatusLabel"] == "Active"
    assert data["client"]["clientProgram"] == "Core Mentoring"
    assert data["client"]["crmCompanyID"] == "acct-Beta Co"
    assert data["contacts"] == [
        {
            "contactName": "Sam Contact",
            "contactEmail": "sam@acme.example",
            "crmContactID": "crm-contact-1",
        }
    ]


def test_rollup_cancelled_session_leaves_immediately(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "erin")
    engagement = _engagement(session, "Gamma LLC", anchor)
    kept = _session_row(session, engagement, _PAST, notes="<p>Kept</p>")
    cancelled = _session_row(session, engagement, _LATER_PAST, notes="<p>Cancelled</p>")
    cancelled.soft_delete(user_id)
    session.commit()

    data = app_client.get(
        f"/engagements/{engagement.engagement_id}/rollup", headers=_headers(user_id)
    ).json()["data"]
    assert [entry["sessionID"] for entry in data["rollup"]] == [str(kept.session_id)]
    assert data["stats"]["totalSessions"] == 1


def test_rollup_scoping_uniform_404_and_leadership_span(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    owner_id, anchor = _mentor(session, roles, "fay")
    other_id, _ = _mentor(session, roles, "gus")
    lead_id = _leadership(session, roles)
    engagement = _engagement(session, "Delta Inc", anchor)
    session.commit()

    path = f"/engagements/{engagement.engagement_id}/rollup"
    assert app_client.get(path, headers=_headers(owner_id)).status_code == 200
    assert app_client.get(path, headers=_headers(lead_id)).status_code == 200
    # Another mentor gets exactly the unknown-id answer — not probeable.
    other = app_client.get(path, headers=_headers(other_id))
    unknown = app_client.get(f"/engagements/{uuid.uuid4()}/rollup", headers=_headers(owner_id))
    assert other.status_code == unknown.status_code == 404
    assert other.json()["errors"][0]["code"] == unknown.json()["errors"][0]["code"]


def test_engagement_list_is_scoped(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    mentor_id, anchor = _mentor(session, roles, "hana")
    lead_id = _leadership(session, roles)
    _engagement(session, "Mine", anchor)
    _engagement(session, "Unassigned", None, status_name="pendingAcceptance")
    session.commit()

    mine = app_client.get("/engagements", headers=_headers(mentor_id)).json()
    assert [row["engagementName"] for row in mine["data"]] == ["Mine"]
    everyone = app_client.get("/engagements", headers=_headers(lead_id)).json()
    assert [row["engagementName"] for row in everyone["data"]] == ["Mine", "Unassigned"]


# --- Lifecycle (WTK-183, REQ-075/REQ-076, DEC-071) --------------------------------------


def _lifecycle(
    app_client: TestClient,
    engagement: Engagement,
    user_id: uuid.UUID,
    transition: str,
    row_version: int = 1,
) -> Any:
    return app_client.post(
        f"/engagements/{engagement.engagement_id}/lifecycle",
        json={"transition": transition, "rowVersion": row_version},
        headers=_headers(user_id),
    )


def test_accept_flips_status_and_names_next_steps(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "ida")
    engagement = _engagement(session, "Pending One", anchor, status_name="pendingAcceptance")
    session.commit()

    response = _lifecycle(app_client, engagement, user_id, "accept")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["engagement"]["engagementStatus"] == "assigned"
    assert "accepted" in body["data"]["confirmation"]
    assert [step["key"] for step in body["data"]["nextSteps"]] == [
        "sendIntroEmail",
        "scheduleFirstSession",
    ]
    assert body["data"]["nextSteps"][0]["templateKey"] == "mentorIntroduction"
    assert body["meta"]["affectedDataSourceKeys"] == [
        DS_MENTOR_ENGAGEMENTS,
        DS_LEADERSHIP_ENGAGEMENTS,
    ]
    # The flip rode the write engine: the history-tracked status change is
    # recorded, not just applied.
    session.expire_all()
    changes = session.scalars(
        select(FieldChange).where(FieldChange.field_name == "engagementStatus")
    ).all()
    assert len(changes) == 1


def test_decline_is_a_status_change_only(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "jules")
    engagement = _engagement(session, "Pending Two", anchor, status_name="pendingAcceptance")
    session.commit()

    body = _lifecycle(app_client, engagement, user_id, "decline").json()
    assert body["data"]["engagement"]["engagementStatus"] == "assignmentDeclined"
    assert "nothing is deleted" in body["data"]["confirmation"].lower()
    # DEC-071: the record survives live — a decline never deletes.
    session.expire_all()
    row = session.get(Engagement, engagement.engagement_id)
    assert row is not None and row.deleted_at is None


def test_hold_and_dormant_transitions(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "kim")
    engagement = _engagement(session, "Active One", anchor, status_name="active")
    session.commit()

    held = _lifecycle(app_client, engagement, user_id, "hold").json()
    assert held["data"]["engagement"]["engagementStatus"] == "onHold"
    dormant = _lifecycle(app_client, engagement, user_id, "dormant", row_version=2).json()
    assert dormant["data"]["engagement"]["engagementStatus"] == "dormant"


def test_invalid_transition_refuses_in_educate_voice(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "lee")
    engagement = _engagement(session, "Active Two", anchor, status_name="active")
    session.commit()

    response = _lifecycle(app_client, engagement, user_id, "accept")
    assert response.status_code == 422
    error = response.json()["errors"][0]
    assert error["code"] == CODE_INVALID_LIFECYCLE_TRANSITION
    # The educate shape: what happened, why (the actual status), what next.
    assert "Accept Assignment ran on 'Active Two'" in error["message"]
    assert "'Active'" in error["message"]
    assert "Pending Acceptance" in error["message"]
    # Nothing moved.
    session.expire_all()
    body = app_client.get(
        f"/engagements/{engagement.engagement_id}/rollup", headers=_headers(user_id)
    ).json()
    assert body["data"]["engagement"]["engagementStatus"] == "active"


def test_unset_status_refuses_with_staff_explanation(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "mia")
    engagement = _engagement(session, "Carried Over", anchor, status_name=None)
    session.commit()

    error = _lifecycle(app_client, engagement, user_id, "accept").json()["errors"][0]
    assert error["code"] == CODE_INVALID_LIFECYCLE_TRANSITION
    assert "status has not been set" in error["message"]


def test_unknown_transition_names_the_vocabulary(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "nia")
    engagement = _engagement(session, "Active Three", anchor)
    session.commit()

    response = _lifecycle(app_client, engagement, user_id, "archive")
    assert response.status_code == 422
    error = response.json()["errors"][0]
    assert error["code"] == CODE_UNKNOWN_LIFECYCLE_TRANSITION
    assert "accept" in error["message"] and "dormant" in error["message"]


def test_lifecycle_stale_row_version_conflicts_with_current_record(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "otto")
    engagement = _engagement(session, "Pending Three", anchor, status_name="pendingAcceptance")
    session.commit()

    response = _lifecycle(app_client, engagement, user_id, "accept", row_version=9)
    assert response.status_code == 409
    body = response.json()
    assert body["errors"][0]["code"] == "staleRowVersion"
    assert body["data"]["engagementName"] == "Pending Three"


def test_lifecycle_is_scoped(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    _, anchor = _mentor(session, roles, "pat")
    other_id, _ = _mentor(session, roles, "quinn")
    engagement = _engagement(session, "Pending Four", anchor, status_name="pendingAcceptance")
    session.commit()

    assert _lifecycle(app_client, engagement, other_id, "accept").status_code == 404


# --- Session create & entry writes (WTK-168/177, REQ-079/082) ---------------------------


def test_session_create_schedules_with_link(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "rae")
    engagement = _engagement(session, "Acme Growth", anchor)
    session.commit()

    response = app_client.post(
        f"/engagements/{engagement.engagement_id}/sessions",
        json={
            "scheduledAt": "2030-09-01T15:00:00Z",
            "conferenceLink": "https://meet.example/2",
        },
        headers=_headers(user_id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["conferenceLink"] == "https://meet.example/2"
    assert body["data"]["rowVersion"] == 1
    assert DS_MENTOR_ENGAGEMENTS in body["meta"]["affectedDataSourceKeys"]
    session.expire_all()
    row = session.scalars(select(MentoringSession)).one()
    scheduled = session.scalars(
        select(OptionValue.option_value_name).where(
            OptionValue.option_value_id == row.session_status
        )
    ).one()
    assert scheduled == "scheduled"


def test_session_create_is_scoped(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    _, anchor = _mentor(session, roles, "sky")
    other_id, _ = _mentor(session, roles, "tam")
    engagement = _engagement(session, "Not Yours", anchor)
    session.commit()

    response = app_client.post(
        f"/engagements/{engagement.engagement_id}/sessions",
        json={"scheduledAt": "2030-09-01T15:00:00Z"},
        headers=_headers(other_id),
    )
    assert response.status_code == 404


def test_session_patch_saves_entry_fields(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "uma")
    engagement = _engagement(session, "Acme Growth", anchor)
    row = _session_row(session, engagement, _PAST)
    session.commit()

    response = app_client.patch(
        f"/sessions/{row.session_id}",
        json={
            "rowVersion": 1,
            "sessionNotes": "<p>Covered pricing</p>",
            "actionItems": "<ul><li>Send deck</li></ul>",
            "conferenceLink": "https://meet.example/3",
        },
        headers=_headers(user_id),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["sessionNotes"] == "<p>Covered pricing</p>"
    assert data["actionItems"] == "<ul><li>Send deck</li></ul>"
    assert data["rowVersion"] == 2


def test_session_patch_status_by_name_and_unknown_name(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "vic")
    engagement = _engagement(session, "Acme Growth", anchor)
    row = _session_row(session, engagement, _PAST)
    session.commit()

    completed = app_client.patch(
        f"/sessions/{row.session_id}",
        json={"rowVersion": 1, "sessionStatus": "completed"},
        headers=_headers(user_id),
    )
    assert completed.status_code == 200

    unknown = app_client.patch(
        f"/sessions/{row.session_id}",
        json={"rowVersion": 2, "sessionStatus": "held"},
        headers=_headers(user_id),
    )
    assert unknown.status_code == 422
    assert unknown.json()["errors"][0]["code"] == CODE_UNKNOWN_SESSION_STATUS


def test_session_patch_stale_version_and_scoping(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "wes")
    other_id, _ = _mentor(session, roles, "xia")
    engagement = _engagement(session, "Acme Growth", anchor)
    row = _session_row(session, engagement, _PAST)
    session.commit()

    stale = app_client.patch(
        f"/sessions/{row.session_id}",
        json={"rowVersion": 5, "sessionNotes": "<p>x</p>"},
        headers=_headers(user_id),
    )
    assert stale.status_code == 409
    foreign = app_client.patch(
        f"/sessions/{row.session_id}",
        json={"rowVersion": 1, "sessionNotes": "<p>x</p>"},
        headers=_headers(other_id),
    )
    assert foreign.status_code == 404


# --- Templated email (WTK-169/178/179, REQ-076/077/084) ---------------------------------


def test_template_list_is_the_staff_catalog(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, _ = _mentor(session, roles, "yara")
    session.commit()

    body = app_client.get("/email/templates", headers=_headers(user_id)).json()
    keys = [entry["templateKey"] for entry in body["data"]]
    assert keys == [template.template_key for template in STAFF_EMAIL_TEMPLATES]
    share = next(e for e in body["data"] if e["templateKey"] == "resourceShare")
    assert "resourceTitle" in share["mergeFields"]


def test_email_preview_then_confirmed_send(
    app_client: TestClient,
    session: Session,
    roles: _StubRoles,
    transport: LoggedEmailTransport,
) -> None:
    user_id, anchor = _mentor(session, roles, "zoe")
    engagement = _engagement(session, "Acme Growth", anchor)
    session.commit()

    preview = app_client.post(
        "/email/send",
        json={
            "templateKey": "mentorIntroduction",
            "engagementID": str(engagement.engagement_id),
        },
        headers=_headers(user_id),
    ).json()["data"]
    # The preview is fully merged and NOTHING was sent.
    assert preview["sent"] is False
    assert preview["to"]["address"] == "sam@acme.example"
    assert "Acme Growth" in preview["subject"]
    assert "Sam Contact" in preview["body"]
    assert "zoe@example.org" in preview["body"]
    assert "{{" not in preview["body"]
    assert transport.sent == []

    sent = app_client.post(
        "/email/send",
        json={
            "templateKey": "mentorIntroduction",
            "engagementID": str(engagement.engagement_id),
            "confirmed": True,
        },
        headers=_headers(user_id),
    ).json()["data"]
    assert sent["sent"] is True
    assert sent["confirmation"] is not None
    # What the mentor previewed is what the transport got — one merge path.
    assert len(transport.sent) == 1
    assert transport.sent[0].subject == preview["subject"]
    assert transport.sent[0].body == preview["body"]


def test_email_refusals(
    app_client: TestClient,
    session: Session,
    roles: _StubRoles,
    transport: LoggedEmailTransport,
) -> None:
    user_id, anchor = _mentor(session, roles, "abe")
    engagement = _engagement(session, "Acme Growth", anchor)
    no_contact = _engagement(session, "No Contact", anchor, contact_email=None)
    session.commit()

    unknown = app_client.post(
        "/email/send",
        json={"templateKey": "nope", "engagementID": str(engagement.engagement_id)},
        headers=_headers(user_id),
    )
    assert unknown.status_code == 422
    assert unknown.json()["errors"][0]["code"] == CODE_UNKNOWN_EMAIL_TEMPLATE

    # The resource template refuses OUTSIDE the share flow: its merge fields
    # (resourceTitle/resourceLocation) only exist on a resource share.
    missing = app_client.post(
        "/email/send",
        json={"templateKey": "resourceShare", "engagementID": str(engagement.engagement_id)},
        headers=_headers(user_id),
    )
    assert missing.status_code == 422
    assert missing.json()["errors"][0]["code"] == CODE_MISSING_MERGE_FIELDS

    nobody = app_client.post(
        "/email/send",
        json={
            "templateKey": "mentorIntroduction",
            "engagementID": str(no_contact.engagement_id),
        },
        headers=_headers(user_id),
    )
    assert nobody.status_code == 422
    assert nobody.json()["errors"][0]["code"] == CODE_NO_CONTACT_EMAIL
    assert transport.sent == []


def test_resource_share_carries_the_link(
    app_client: TestClient,
    session: Session,
    roles: _StubRoles,
    transport: LoggedEmailTransport,
) -> None:
    user_id, anchor = _mentor(session, roles, "bea")
    engagement = _engagement(session, "Acme Growth", anchor)
    resource = Resource(
        resource_title="Pricing Guide",
        resource_location="https://library.example/pricing-guide.pdf",
    )
    session.add(resource)
    session.commit()

    preview = app_client.post(
        f"/resources/{resource.resource_id}/share",
        json={"engagementID": str(engagement.engagement_id)},
        headers=_headers(user_id),
    ).json()["data"]
    assert preview["sent"] is False
    assert "Pricing Guide" in preview["subject"]
    assert "https://library.example/pricing-guide.pdf" in preview["body"]
    assert transport.sent == []

    app_client.post(
        f"/resources/{resource.resource_id}/share",
        json={"engagementID": str(engagement.engagement_id), "confirmed": True},
        headers=_headers(user_id),
    )
    assert len(transport.sent) == 1
    assert "https://library.example/pricing-guide.pdf" in transport.sent[0].body


def test_resource_share_unknown_resource_is_404(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "cal")
    engagement = _engagement(session, "Acme Growth", anchor)
    session.commit()

    response = app_client.post(
        f"/resources/{uuid.uuid4()}/share",
        json={"engagementID": str(engagement.engagement_id)},
        headers=_headers(user_id),
    )
    assert response.status_code == 404


# --- The record windows now resolve domain entities (WTK-168) ---------------------------


def test_record_preview_serves_domain_entities_through_production_wiring(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "dee")
    engagement = _engagement(session, "Acme Growth", anchor)
    session.commit()

    response = app_client.get(
        f"/records/engagement/{engagement.engagement_id}/preview",
        headers=_headers(user_id),
    )
    assert response.status_code == 200
    record = response.json()["data"]["record"]
    assert record["engagementName"] == "Acme Growth"
    client_id = record["clientID"]
    client_preview = app_client.get(
        f"/records/client/{client_id}/preview", headers=_headers(user_id)
    )
    assert client_preview.status_code == 200


# --- Conferencing + invite automation (WTK-170, REQ-078/080) ----------------------------


def test_scheduling_books_org_meeting_sends_invite_and_queues_retrieval(
    app_client: TestClient,
    session: Session,
    roles: _StubRoles,
    transport: LoggedEmailTransport,
) -> None:
    user_id, anchor = _mentor(session, roles, "ana")
    engagement = _engagement(session, "Acme Growth", anchor)
    session.commit()

    response = app_client.post(
        f"/engagements/{engagement.engagement_id}/sessions",
        json={"scheduledAt": "2030-09-01T15:00:00Z"},
        headers=_headers(user_id),
    )
    assert response.status_code == 200
    body = response.json()
    # The fake provider's deterministic booking filled the link + identifier.
    assert body["data"]["conferenceLink"].startswith("https://conference.dev.invalid/m/")
    assert body["data"]["externalMeetingID"].startswith("dev-meeting-")
    # The invite rode the one email seam, carrying the conference link.
    assert body["meta"]["invite"]["sent"] is True
    assert body["meta"]["invite"]["toAddress"] == "sam@acme.example"
    (invite,) = transport.sent
    assert invite.template_key == "sessionInvite"
    assert body["data"]["conferenceLink"] in invite.body
    assert "2030-09-01 15:00 UTC" in invite.body
    assert "{{" not in invite.body
    # The transcript retrieval job is queued for AFTER the session.
    session.expire_all()
    job = session.scalars(
        select(BackgroundJob).where(BackgroundJob.job_type == TRANSCRIPT_RETRIEVAL_JOB_TYPE)
    ).one()
    assert job.job_payload == {"sessionID": body["data"]["sessionID"]}
    assert as_utc(job.run_after) == datetime(2030, 9, 1, 15, 0, tzinfo=UTC) + (
        TRANSCRIPT_RETRIEVAL_DELAY
    )


def test_scheduling_with_pasted_link_books_nothing(
    app_client: TestClient,
    session: Session,
    roles: _StubRoles,
    transport: LoggedEmailTransport,
) -> None:
    user_id, anchor = _mentor(session, roles, "bea")
    engagement = _engagement(session, "Acme Growth", anchor)
    session.commit()

    body = app_client.post(
        f"/engagements/{engagement.engagement_id}/sessions",
        json={
            "scheduledAt": "2030-09-01T15:00:00Z",
            "conferenceLink": "https://meet.example/9",
        },
        headers=_headers(user_id),
    ).json()
    # REQ-079's paste path: the link is taken verbatim, no org meeting exists,
    # so no external identifier and no transcript automation — but the invite
    # still goes out carrying the pasted link.
    assert body["data"]["conferenceLink"] == "https://meet.example/9"
    assert body["data"]["externalMeetingID"] is None
    assert body["meta"]["invite"]["sent"] is True
    assert "https://meet.example/9" in transport.sent[0].body
    assert session.scalars(select(BackgroundJob)).all() == []


def test_scheduling_without_contact_email_reports_the_skip(
    app_client: TestClient,
    session: Session,
    roles: _StubRoles,
    transport: LoggedEmailTransport,
) -> None:
    user_id, anchor = _mentor(session, roles, "cal")
    engagement = _engagement(session, "Acme Growth", anchor, contact_email=None)
    session.commit()

    body = app_client.post(
        f"/engagements/{engagement.engagement_id}/sessions",
        json={"scheduledAt": "2030-09-01T15:00:00Z"},
        headers=_headers(user_id),
    ).json()
    # Scheduling never fails for a missing invite target; the skip is honest.
    assert body["data"]["sessionID"]
    assert body["meta"]["invite"]["sent"] is False
    assert "no primary contact email" in body["meta"]["invite"]["reason"]
    assert transport.sent == []


class _FailingProvider:
    """A provider whose platform is down — the automated path degrades."""

    def create_meeting(self, context: object) -> object:
        raise RuntimeError("conference platform unavailable")


def test_provider_failure_degrades_to_the_paste_path(
    app_client: TestClient,
    session: Session,
    roles: _StubRoles,
    transport: LoggedEmailTransport,
) -> None:
    app_client.app.dependency_overrides[get_conferencing_provider] = _FailingProvider  # type: ignore[attr-defined]
    user_id, anchor = _mentor(session, roles, "dot")
    engagement = _engagement(session, "Acme Growth", anchor)
    session.commit()

    response = app_client.post(
        f"/engagements/{engagement.engagement_id}/sessions",
        json={"scheduledAt": "2030-09-01T15:00:00Z"},
        headers=_headers(user_id),
    )
    # The scheduling itself succeeds (REQ-079 is the universal fallback);
    # the invite skip explains what to do next.
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["conferenceLink"] is None
    assert body["data"]["externalMeetingID"] is None
    assert body["meta"]["invite"]["sent"] is False
    assert "Paste a meeting link" in body["meta"]["invite"]["reason"]
    assert transport.sent == []


# --- Transcript retrieval + drafts (WTK-180/181, REQ-083) --------------------------------


def _app_created_session(
    session: Session, engagement: Engagement, *, notes: str | None = None
) -> MentoringSession:
    row = _session_row(session, engagement, _PAST, notes=notes)
    row.external_meeting_id = "dev-meeting-test"
    session.flush()
    return row


def test_transcript_retrieval_attaches_and_drafts_propose(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "eve")
    engagement = _engagement(session, "Acme Growth", anchor)
    row = _app_created_session(session, engagement, notes="<p>My own notes</p>")
    session.commit()

    response = app_client.post(
        f"/sessions/{row.session_id}/transcript", headers=_headers(user_id)
    )
    assert response.status_code == 200
    data = response.json()["data"]
    # The transcript attached with its provenance; the drafts landed as
    # PROPOSALS — the mentor's own notes are untouched (REQ-083 authorship).
    assert "dev transcript for dev-meeting-test" in data["transcriptText"]
    assert data["transcriptSource"] == "devTranscript"
    assert data["draftSummary"].startswith("<p>")
    assert "Q3 hiring plan" in data["draftActionItems"]
    assert data["sessionNotes"] == "<p>My own notes</p>"
    assert data["actionItems"] is None


def test_transcript_retrieval_refuses_for_pasted_links(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "fay")
    engagement = _engagement(session, "Acme Growth", anchor)
    row = _session_row(session, engagement, _PAST, link="https://meet.example/5")
    session.commit()

    response = app_client.post(
        f"/sessions/{row.session_id}/transcript", headers=_headers(user_id)
    )
    assert response.status_code == 422
    error = response.json()["errors"][0]
    assert error["code"] == CODE_NO_APP_CREATED_MEETING
    assert "Paste the transcript" in error["message"]


class _NotReadySource:
    """The platform hasn't produced the transcript yet."""

    def retrieve(self, external_meeting_id: str) -> None:
        return None


def test_transcript_not_ready_educates(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    app_client.app.dependency_overrides[get_transcript_source] = _NotReadySource  # type: ignore[attr-defined]
    user_id, anchor = _mentor(session, roles, "gil")
    engagement = _engagement(session, "Acme Growth", anchor)
    row = _app_created_session(session, engagement)
    session.commit()

    response = app_client.post(
        f"/sessions/{row.session_id}/transcript", headers=_headers(user_id)
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == CODE_TRANSCRIPT_NOT_READY


def test_transcript_retrieval_is_scoped(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    _, anchor = _mentor(session, roles, "hal")
    other_id, _ = _mentor(session, roles, "ida")
    engagement = _engagement(session, "Not Yours", anchor)
    row = _app_created_session(session, engagement)
    session.commit()

    response = app_client.post(
        f"/sessions/{row.session_id}/transcript", headers=_headers(other_id)
    )
    assert response.status_code == 404


def test_transcript_paste_path_appends_and_rewrite_refuses(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "joy")
    engagement = _engagement(session, "Acme Growth", anchor)
    row = _session_row(session, engagement, _PAST, link="https://meet.example/7")
    session.commit()

    pasted = app_client.patch(
        f"/sessions/{row.session_id}",
        json={"rowVersion": 1, "transcriptText": "Line one.", "transcriptSource": "pasted"},
        headers=_headers(user_id),
    )
    assert pasted.status_code == 200
    assert pasted.json()["data"]["transcriptText"] == "Line one."
    assert pasted.json()["data"]["transcriptSource"] == "pasted"

    extended = app_client.patch(
        f"/sessions/{row.session_id}",
        json={"rowVersion": 2, "transcriptText": "Line one.\nLine two."},
        headers=_headers(user_id),
    )
    assert extended.status_code == 200

    rewrite = app_client.patch(
        f"/sessions/{row.session_id}",
        json={"rowVersion": 3, "transcriptText": "Something else entirely."},
        headers=_headers(user_id),
    )
    assert rewrite.status_code == 422
    error = rewrite.json()["errors"][0]
    assert error["code"] == CODE_TRANSCRIPT_APPEND_ONLY
    assert "append-only" in error["message"]


def test_draft_acceptance_rides_the_normal_patch(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "kim")
    engagement = _engagement(session, "Acme Growth", anchor)
    row = _app_created_session(session, engagement)
    session.commit()
    app_client.post(f"/sessions/{row.session_id}/transcript", headers=_headers(user_id))

    session.expire_all()
    drafted = session.get(MentoringSession, row.session_id)
    assert drafted is not None and drafted.draft_summary is not None
    draft_summary, draft_items = drafted.draft_summary, drafted.draft_action_items
    # The mentor accepts: the client copies the draft into the entry fields
    # and clears the proposal — one normal PATCH, mentor as author of record.
    accepted = app_client.patch(
        f"/sessions/{row.session_id}",
        json={
            "rowVersion": drafted.row_version,
            "sessionNotes": draft_summary,
            "actionItems": draft_items,
            "draftSummary": None,
            "draftActionItems": None,
        },
        headers=_headers(user_id),
    )
    assert accepted.status_code == 200
    data = accepted.json()["data"]
    assert data["sessionNotes"] == draft_summary
    assert data["draftSummary"] is None


def test_transcript_retrieval_job_attaches_and_retries(
    session: Session, roles: _StubRoles
) -> None:
    # The registry seed the app fixture runs is needed for the write engine.
    seed_built_in_registry(
        session, [Client, CrmCompanyRef, CrmMentorRef, Engagement, MentoringSession]
    )
    _, anchor = _mentor(session, roles, "lyn")
    engagement = _engagement(session, "Acme Growth", anchor)
    row = _app_created_session(session, engagement)
    job = enqueue_job(
        session, TRANSCRIPT_RETRIEVAL_JOB_TYPE, {"sessionID": str(row.session_id)}
    )
    session.commit()

    # Not ready yet → transient failure: the job goes back to pending with
    # backoff, never parks on the first miss.
    handlers = {
        TRANSCRIPT_RETRIEVAL_JOB_TYPE: transcript_retrieval_job(
            _NotReadySource(), FakeSummaryDrafter()
        )
    }
    assert process_next_job(session, handlers) is True
    session.commit()
    assert job.job_status == "pending"
    assert job.attempt_count == 1

    # Ready → the attach rule runs and the job completes.
    job.run_after = utcnow()
    session.commit()
    handlers = {
        TRANSCRIPT_RETRIEVAL_JOB_TYPE: transcript_retrieval_job(
            FakeTranscriptSource(), FakeSummaryDrafter()
        )
    }
    assert process_next_job(session, handlers) is True
    session.commit()
    session.expire_all()
    assert job.job_status == "completed"
    refreshed = session.get(MentoringSession, row.session_id)
    assert refreshed is not None
    assert refreshed.transcript_source == "devTranscript"
    assert refreshed.draft_summary is not None


def test_transcript_retrieval_job_parks_without_a_meeting(
    session: Session, roles: _StubRoles
) -> None:
    seed_built_in_registry(
        session, [Client, CrmCompanyRef, CrmMentorRef, Engagement, MentoringSession]
    )
    _, anchor = _mentor(session, roles, "moe")
    engagement = _engagement(session, "Acme Growth", anchor)
    row = _session_row(session, engagement, _PAST, link="https://meet.example/8")
    job = enqueue_job(
        session, TRANSCRIPT_RETRIEVAL_JOB_TYPE, {"sessionID": str(row.session_id)}
    )
    session.commit()

    handlers = {
        TRANSCRIPT_RETRIEVAL_JOB_TYPE: transcript_retrieval_job(
            FakeTranscriptSource(), FakeSummaryDrafter()
        )
    }
    assert process_next_job(session, handlers) is True
    session.commit()
    # A pasted-link session can never grow a platform meeting: parked, not retried.
    assert job.job_status == "needsAttention"


# --- The WTK-172 lifecycle residual: resume -----------------------------------------------


def test_resume_reactivates_a_paused_engagement(
    app_client: TestClient, session: Session, roles: _StubRoles
) -> None:
    user_id, anchor = _mentor(session, roles, "nia")
    engagement = _engagement(session, "Acme Growth", anchor, status_name="onHold")
    session.commit()

    response = _lifecycle(app_client, engagement, user_id, "resume")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["engagement"]["engagementStatus"] == "active"
    assert "Active again" in body["data"]["confirmation"]

    # And an Active engagement cannot "resume" — the refusal educates.
    response = app_client.post(
        f"/engagements/{engagement.engagement_id}/lifecycle",
        json={"transition": "resume", "rowVersion": 2},
        headers=_headers(user_id),
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == CODE_INVALID_LIFECYCLE_TRANSITION
