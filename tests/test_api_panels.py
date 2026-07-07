"""The WTK-233 panel catalog: areas served, landing view, isolation (WTK-189/190).

Drives the catalog end to end through production wiring where it matters:
``/panels/{key}/grid`` serves the REQ-071 panels over the seeded sources
(view keys = data-source keys, which is what activates the client's
mentoring actions and the engagement preview), ``/panels/{key}/rows`` rides
the grant-enforced stored-source path (mentor A structurally cannot read
mentor B's rows; leadership spans mentors), ``GET /shell`` declares the
Areas and the REQ-072 startup default, ``GET /home`` composes the rail from
the same catalog, and the stored role source reads the newest live login
capture.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from identity_stub import header_user_id
from mentorapp.access.mentoring import (
    DS_LEADERSHIP_ENGAGEMENTS,
    DS_MENTOR_ENGAGEMENTS,
    LEADERSHIP_ROLE,
    MENTOR_ROLE,
    seed_mentor_access,
)
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.panel_catalog import (
    MENTOR_PANELS,
    MentorPanelCatalog,
    StoredSessionRoleSource,
    column_label,
    seed_startup_default,
)
from mentorapp.api.routers.panels import CODE_UNKNOWN_PANEL_VIEW, CODE_UNKNOWN_SORT_FIELD
from mentorapp.api.routers.shell import get_shell_catalog
from mentorapp.api.routers.workprocess import get_role_source
from mentorapp.main import create_app
from mentorapp.storage import (
    AppUser,
    AuthSession,
    Client,
    CrmCompanyRef,
    CrmMentorRef,
    Engagement,
    Event,
    MentoringSession,
    Resource,
    UserPreference,
    regenerate_read_views,
    seed_built_in_registry,
    utcnow,
)
from mentorapp.storage.mentoring import ENGAGEMENT_STATUS_VALUES
from mentorapp.storage.theming import (
    CONDITION_OPERATORS,
    FORMATTING_EFFECTS,
    STATUS_COLOR_SLOTS,
)
from mentorapp.ui.home_panel import STARTUP_PREFERENCE_KEY

_PAST = datetime(2026, 1, 10, 15, 0, tzinfo=UTC)


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
    # The D9 identity seam resolves sessions in production; these are not
    # session-lifecycle tests, so the stub names the acting user directly.
    app.dependency_overrides[get_current_user_id] = header_user_id
    app.dependency_overrides[get_role_source] = lambda: roles
    # The shell/home catalogs construct their own stored role source in
    # production; tests bind the SAME catalog over the stub roles so every
    # seam agrees with the role fixture.
    app.dependency_overrides[get_shell_catalog] = lambda: MentorPanelCatalog(session, roles)
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


def _engagement(session: Session, name: str, mentor: CrmMentorRef) -> Engagement:
    company = CrmCompanyRef(crm_company_id=f"acct-{name}")
    session.add(company)
    session.flush()
    client = Client(crm_company_ref_id=company.crm_company_ref_id)
    session.add(client)
    session.flush()
    engagement = Engagement(
        engagement_name=name,
        client_id=client.client_id,
        crm_mentor_ref_id=mentor.crm_mentor_ref_id,
        primary_contact_name=f"Contact {name}",
        primary_contact_email=f"contact@{name.lower().replace(' ', '')}.example",
    )
    session.add(engagement)
    session.flush()
    return engagement


@pytest.fixture()
def seeded(session: Session, roles: _StubRoles) -> dict[str, uuid.UUID]:
    """Seeded areas + two mentors with an engagement each + a leadership user."""
    seed_built_in_registry(
        session,
        [Client, CrmCompanyRef, CrmMentorRef, Engagement, Event, MentoringSession, Resource],
    )
    regenerate_read_views(session)
    seed_mentor_access(session)
    seed_startup_default(session)
    mentor_a, anchor_a = _mentor(session, roles, "alice")
    mentor_b, anchor_b = _mentor(session, roles, "bram")
    lead = _leadership(session, roles)
    acme = _engagement(session, "Acme Growth", anchor_a)
    _engagement(session, "Zenith Scaleup", anchor_b)
    session.add(MentoringSession(engagement_id=acme.engagement_id, scheduled_at=_PAST))
    session.add(
        Resource(
            resource_title="Pricing worksheet",
            resource_location="https://drive.example/pricing.xlsx",
        )
    )
    session.commit()
    return {"mentorA": mentor_a, "mentorB": mentor_b, "lead": lead}


# --- The panel view-model (WTK-233) ------------------------------------------------------


def test_engagements_panel_serves_the_landing_view(
    app_client: TestClient, seeded: dict[str, uuid.UUID]
) -> None:
    body = app_client.get("/panels/engagements/grid", headers=_headers(seeded["mentorA"]))
    assert body.status_code == 200
    data = body.json()["data"]
    assert data["gridId"] == "engagements"
    # REQ-072: the mentor lands on "My Active Engagements"; the leadership
    # span is not among a mentor's views.
    assert data["activeViewKey"] == DS_MENTOR_ENGAGEMENTS
    assert [v["viewKey"] for v in data["views"]] == [DS_MENTOR_ENGAGEMENTS]
    (view,) = data["views"]
    assert view["label"] == "My Active Engagements"
    # The dataSourceKey is the seeded key VERBATIM — what activates the
    # client-side mentoring actions and the engagement preview seam.
    assert view["dataSourceKey"] == DS_MENTOR_ENGAGEMENTS
    # The REQ-072 ruled columns in requirement order — the engagement ID is
    # the row key (rows[].recordId), never a rendered column, and the REQ-019
    # scoping column stays off-grid too (FND-909 D2).
    names = [c["fieldName"] for c in data["columns"]]
    assert names == [
        "engagementName",
        "engagementStatusLabel",
        "primaryContactName",
        "primaryContactEmail",
        "lastSessionAt",
        "nextSessionAt",
        "totalSessions",
        "openActionItems",
    ]
    # Headers speak the ruled wording — "Last Session", never the derived
    # "Last Session At" (FND-909 D11).
    labels = {c["fieldName"]: c["label"] for c in data["columns"]}
    assert labels["engagementStatusLabel"] == "Engagement Status"
    assert labels["lastSessionAt"] == "Last Session"
    assert labels["nextSessionAt"] == "Next Session"
    assert labels["totalSessions"] == "Total Sessions"
    assert labels["openActionItems"] == "Open Action Items"
    # Each column declares its format kind so the client's one formatter can
    # render the value (FND-909 D1) — dates/datetimes never reach the grid raw.
    formats = {c["fieldName"]: c["format"] for c in data["columns"]}
    assert formats["engagementName"] == "text"
    assert formats["lastSessionAt"] == "datetime"
    assert formats["nextSessionAt"] == "datetime"
    assert formats["totalSessions"] == "number"


def test_engagements_panel_serves_the_seeded_formatting_rules(
    app_client: TestClient, seeded: dict[str, uuid.UUID]
) -> None:
    """The view's REQ-045 rules ride the panel payload (FND-909 D7).

    Status values must render as slot-colored chips, never plain text, so
    the grid payload carries the triage views' seeded rules in declared
    order (= first-match-wins evaluation order), each conditioning on the
    status label with a standard operator and painting a FIXED status slot,
    never a literal color (FND-906).
    """
    data = app_client.get(
        "/panels/engagements/grid", headers=_headers(seeded["mentorA"])
    ).json()["data"]
    rules = data["formattingRules"]
    assert [(r["conditionValue"], r["effectSlot"]) for r in rules] == [
        ("Pending Acceptance", "statusWarning"),
        ("Assigned", "statusPositive"),
        ("Active", "statusPositive"),
        ("On Hold", "statusWarning"),
        ("Dormant", "statusNegative"),
        ("Assignment Declined", "statusNegative"),
    ]
    # Every seeded status label carries a rule — no engagement status is
    # left rendering as D7's plain text — and every rule speaks the
    # persisted vocabularies verbatim (one home: storage.theming).
    assert {r["conditionValue"] for r in rules} == {
        label for _, label in ENGAGEMENT_STATUS_VALUES
    }
    for rule in rules:
        assert rule["conditionField"] == "engagementStatusLabel"
        assert rule["conditionOperator"] in CONDITION_OPERATORS
        assert rule["effect"] in FORMATTING_EFFECTS
        assert rule["effectSlot"] in STATUS_COLOR_SLOTS
    # Leadership's "All Engagements" reads the same triage projection, so
    # its source declares the SAME rules — the two views cannot drift.
    lead_data = app_client.get(
        "/panels/engagements/grid", headers=_headers(seeded["lead"])
    ).json()["data"]
    assert lead_data["formattingRules"] == rules
    # A source with no declared rules serves an empty list, not a missing key.
    contacts = app_client.get(
        "/panels/contacts/grid", headers=_headers(seeded["mentorA"])
    ).json()["data"]
    assert contacts["formattingRules"] == []


def test_leadership_sees_both_engagement_views(
    app_client: TestClient, seeded: dict[str, uuid.UUID]
) -> None:
    data = app_client.get("/panels/engagements/grid", headers=_headers(seeded["lead"])).json()[
        "data"
    ]
    assert [v["viewKey"] for v in data["views"]] == [
        DS_MENTOR_ENGAGEMENTS,
        DS_LEADERSHIP_ENGAGEMENTS,
    ]


def test_unknown_panel_is_404_and_ungranted_caller_gets_403(
    app_client: TestClient, seeded: dict[str, uuid.UUID]
) -> None:
    assert (
        app_client.get("/panels/nonesuch/grid", headers=_headers(seeded["mentorA"])).status_code
        == 404
    )
    # A caller with no roles holds no grants: the standard 403 envelope
    # (REQ-006), not an empty grid and not a probeable 404.
    stranger = uuid.uuid4()
    response = app_client.get("/panels/engagements/grid", headers=_headers(stranger))
    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "dataSourceAccessDenied"
    assert (
        app_client.get("/panels/engagements/rows", headers=_headers(stranger)).status_code
        == 403
    )
    assert (
        app_client.get("/panels/engagements/aggregates", headers=_headers(stranger)).status_code
        == 403
    )


# --- Rows: isolation, search, sort (WTK-190) ---------------------------------------------


def test_rows_are_mentor_isolated_and_leadership_spans(
    app_client: TestClient, seeded: dict[str, uuid.UUID]
) -> None:
    rows_a = app_client.get(
        "/panels/engagements/rows", headers=_headers(seeded["mentorA"])
    ).json()["data"]["rows"]
    assert [row["title"] for row in rows_a] == ["Acme Growth"]
    assert rows_a[0]["values"]["totalSessions"] == 1
    # The engagement's id serves as the row key for selection/preview, and
    # ONLY as the row key — it is not projected as a column (FND-909 D2).
    assert rows_a[0]["recordId"]
    assert "engagementID" not in rows_a[0]["values"]

    # Mentor B's own scope: their engagement only — mentor A's rows are
    # structurally unreachable (the REQ-019 injected filter, WTK-186).
    rows_b = app_client.get(
        "/panels/engagements/rows", headers=_headers(seeded["mentorB"])
    ).json()["data"]["rows"]
    assert [row["title"] for row in rows_b] == ["Zenith Scaleup"]

    # Leadership's All Engagements view spans both mentors.
    rows_lead = app_client.get(
        f"/panels/engagements/rows?view={DS_LEADERSHIP_ENGAGEMENTS}",
        headers=_headers(seeded["lead"]),
    ).json()["data"]["rows"]
    assert sorted(row["title"] for row in rows_lead) == ["Acme Growth", "Zenith Scaleup"]

    # A mentor addressing the leadership view hits the grant boundary: 403.
    denied = app_client.get(
        f"/panels/engagements/rows?view={DS_LEADERSHIP_ENGAGEMENTS}",
        headers=_headers(seeded["mentorA"]),
    )
    assert denied.status_code == 403


def test_rows_search_sort_and_refusals(
    app_client: TestClient, seeded: dict[str, uuid.UUID]
) -> None:
    lead = seeded["lead"]
    base = f"/panels/engagements/rows?view={DS_LEADERSHIP_ENGAGEMENTS}"
    # Search narrows across displayed columns at three characters and up.
    hit = app_client.get(f"{base}&search=zen", headers=_headers(lead)).json()
    assert [row["title"] for row in hit["data"]["rows"]] == ["Zenith Scaleup"]
    assert hit["meta"]["searchApplied"] is True
    # Sub-minimum text never armed the search (REQ-020).
    unarmed = app_client.get(f"{base}&search=ze", headers=_headers(lead)).json()
    assert unarmed["meta"]["searchApplied"] is False
    assert len(unarmed["data"]["rows"]) == 2
    # Header sort applies over the projection.
    ordered = app_client.get(f"{base}&sort=engagementName:desc", headers=_headers(lead)).json()[
        "data"
    ]["rows"]
    assert [row["title"] for row in ordered] == ["Zenith Scaleup", "Acme Growth"]
    # Refusals: unknown view and unknown sort field are per-field 422s.
    unknown_view = app_client.get(
        "/panels/engagements/rows?view=nonesuch", headers=_headers(lead)
    )
    assert unknown_view.status_code == 422
    assert unknown_view.json()["errors"][0]["code"] == CODE_UNKNOWN_PANEL_VIEW
    bad_sort = app_client.get(f"{base}&sort=nonesuch:asc", headers=_headers(lead))
    assert bad_sort.status_code == 422
    assert bad_sort.json()["errors"][0]["code"] == CODE_UNKNOWN_SORT_FIELD


def test_aggregates_report_the_search_gap(
    app_client: TestClient, seeded: dict[str, uuid.UUID]
) -> None:
    lead = seeded["lead"]
    data = app_client.get(
        f"/panels/engagements/aggregates?view={DS_LEADERSHIP_ENGAGEMENTS}&search=zen",
        headers=_headers(lead),
    ).json()["data"]
    # totalCount is the narrowed set; unnarrowedCount the whole view — the
    # status bar's "N rows hidden by search" honesty gap (REQ-026). The
    # footer carries the view's declared count aggregate over the SAME
    # narrowed set, keyed by its column (SKL-112 / FND-909 D11).
    assert data == {
        "totalCount": 1,
        "unnarrowedCount": 2,
        "footer": {"engagementName": "1"},
    }


def test_resources_panel_serves_the_library_readonly_surface(
    app_client: TestClient, seeded: dict[str, uuid.UUID]
) -> None:
    rows = app_client.get("/panels/resources/rows", headers=_headers(seeded["mentorA"])).json()[
        "data"
    ]["rows"]
    assert [row["title"] for row in rows] == ["Pricing worksheet"]


# --- The shell declares the areas and the startup target (WTK-233) -----------------------


def test_shell_declares_areas_and_the_mentor_landing(
    app_client: TestClient, session: Session, seeded: dict[str, uuid.UUID]
) -> None:
    data = app_client.get("/shell", headers=_headers(seeded["mentorA"])).json()["data"]
    # Home FIRST (FND-909 D8, REQ-011: the prototype's navigation leads with
    # Home), then the seven REQ-071 areas, server-decided, in requirement
    # order, each naming the panel and its default view.
    assert [area["panelKey"] for area in data["areas"]] == [
        "home",
        "contacts",
        "companies",
        "clients",
        "engagements",
        "sessions",
        "resources",
        "events",
    ]
    # Home opens the home panel itself, never a grid view.
    assert data["areas"][0] == {"panelKey": "home", "label": "Home", "viewKey": None}
    engagements = next(a for a in data["areas"] if a["panelKey"] == "engagements")
    assert engagements["viewKey"] == DS_MENTOR_ENGAGEMENTS
    # The REQ-072 ruling: the seeded org default lands mentors on the
    # engagements panel.
    assert data["startup"] == {"panelKey": "engagements", "notice": None}

    # A user's OWN startup row overrides the org default (REQ-060).
    session.add(
        UserPreference(
            user_id=seeded["mentorA"],
            preference_key=STARTUP_PREFERENCE_KEY,
            preference_value={"choice": "home"},
        )
    )
    session.commit()
    data = app_client.get("/shell", headers=_headers(seeded["mentorA"])).json()["data"]
    assert data["startup"]["panelKey"] == "home"

    # A role-less caller keeps Home (it needs no grant) and nothing else, and
    # lands on Home with the educate notice — the org default names a panel
    # they cannot open.
    stranger = uuid.uuid4()
    data = app_client.get("/shell", headers=_headers(stranger)).json()["data"]
    assert [area["panelKey"] for area in data["areas"]] == ["home"]
    assert data["startup"]["panelKey"] == "home"
    assert data["startup"]["notice"] is not None


def test_home_rail_derives_from_the_same_catalog(
    app_client: TestClient, seeded: dict[str, uuid.UUID]
) -> None:
    # /home runs the production-wired home catalog (install_panel_wiring
    # binds it in create_app); the stub roles ride the overridden role seam
    # only for /panels — home reads the stored capture, so give the mentor a
    # live session row carrying the Mentor role.
    body = app_client.get("/home", headers=_headers(seeded["mentorA"]))
    assert body.status_code == 200


def test_home_catalog_answers_home_first_then_granted_areas(
    session: Session, roles: _StubRoles, seeded: dict[str, uuid.UUID]
) -> None:
    catalog = MentorPanelCatalog(session, roles)
    keys = catalog.accessible_panel_keys(seeded["mentorA"])
    assert keys[0] == "home"
    assert list(keys[1:]) == [panel.panel_key for panel in MENTOR_PANELS]
    # Dashlet availability mirrors the same grants.
    available = catalog.available_view_keys(seeded["mentorA"])
    assert DS_MENTOR_ENGAGEMENTS in available
    assert DS_LEADERSHIP_ENGAGEMENTS not in available
    # No roles, no areas — Home alone.
    assert catalog.accessible_panel_keys(uuid.uuid4()) == ("home",)


# --- The stored role source (production RoleSource) --------------------------------------


def _auth_session(
    session: Session, user_id: uuid.UUID, *, state: str, roles: list[str], created: datetime
) -> None:
    row = AuthSession(
        user_id=user_id,
        session_secret_hash="0" * 64,
        session_state=state,
        session_expires_at=utcnow() + timedelta(hours=8),
        session_role_names=roles,
    )
    row.created_at = created
    session.add(row)
    session.flush()


def test_stored_role_source_reads_the_newest_live_capture(session: Session) -> None:
    user = AppUser(crm_user_id="crm-rey", username="rey@example.org")
    session.add(user)
    session.flush()
    source = StoredSessionRoleSource(session)
    # No sessions: deny by default.
    assert source.user_roles(user.user_id) == frozenset()
    # The newest un-ended capture wins; ended sessions never speak.
    _auth_session(session, user.user_id, state="active", roles=["Mentor"], created=_PAST)
    _auth_session(
        session,
        user.user_id,
        state="active",
        roles=["Mentor", "Leadership"],
        created=_PAST + timedelta(days=1),
    )
    _auth_session(
        session,
        user.user_id,
        state="ended",
        roles=["Administrator"],
        created=_PAST + timedelta(days=2),
    )
    session.commit()
    assert source.user_roles(user.user_id) == frozenset({"Mentor", "Leadership"})


def test_column_label_speaks_human() -> None:
    assert column_label("engagementStatusLabel") == "Engagement Status"
    assert column_label("crmCompanyID") == "CRM Company ID"
    assert column_label("primaryContactName") == "Primary Contact Name"
    assert column_label("scheduledAt") == "Scheduled At"
