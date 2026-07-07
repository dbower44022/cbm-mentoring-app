"""``/shell`` — headers, quick-open, navigation over the envelope (REQ-009, REQ-010)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from identity_stub import header_user_id
from mentorapp.access.grants import GrantLookup, InMemoryGrantRegistry, SourceGrant
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.routers.shell import get_shell_catalog
from mentorapp.main import create_app
from mentorapp.storage import AppUser, Notification, UserPreference, utcnow, uuid7
from mentorapp.ui.home_panel import HOME_FRAME
from mentorapp.ui.navigation import (
    HOME_PANEL,
    NAVIGATION_PREFERENCE_KEY,
    Panel,
    PanelType,
    ViewRecord,
)

MENTORS_PANEL = Panel("mentors", "Mentors", PanelType.GRID, data_source_key="ds.mentors")
ADMIN_PANEL = Panel("admin", "Admin", PanelType.GRID, data_source_key="ds.admin")

ACTIVE_VIEW = ViewRecord("views.activeMentors", "Active Mentors", "mentors")
REMOVED_VIEW = ViewRecord(
    "views.retiredMentors",
    "Retired Mentors",
    "mentors",
    deleted_at=utcnow(),
    deleted_by="Dana Admin",
)
ADMIN_VIEW = ViewRecord("views.adminUsers", "Admin Users", "admin")


@dataclass(frozen=True)
class StubShellCatalog:
    """Fixed panels/views plus a grant registry — the seam, stubbed."""

    registry: InMemoryGrantRegistry
    roles: frozenset[str] = frozenset({"mentor"})

    def panel(self, panel_key: str) -> Panel | None:
        return self._panels().get(panel_key)

    def view(self, view_key: str) -> ViewRecord | None:
        return self._views().get(view_key)

    def panels(self) -> tuple[Panel, ...]:
        return tuple(self._panels().values())

    def views(self) -> tuple[ViewRecord, ...]:
        return tuple(self._views().values())

    def grants(self) -> GrantLookup:
        return self.registry

    def user_roles(self, user_id: uuid.UUID) -> frozenset[str]:
        return self.roles

    @staticmethod
    def _panels() -> dict[str, Panel]:
        return {p.panel_key: p for p in (HOME_PANEL, MENTORS_PANEL, ADMIN_PANEL)}

    @staticmethod
    def _views() -> dict[str, ViewRecord]:
        return {v.view_key: v for v in (ACTIVE_VIEW, REMOVED_VIEW, ADMIN_VIEW)}


@pytest.fixture()
def client(session: Session) -> TestClient:
    registry = InMemoryGrantRegistry([SourceGrant("ds.mentors", "mentor")])
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    # The D9 identity seam resolves sessions in production; these are not
    # session-lifecycle tests, so the stub names the acting user directly.
    app.dependency_overrides[get_current_user_id] = header_user_id
    app.dependency_overrides[get_shell_catalog] = lambda: StubShellCatalog(registry)
    return TestClient(app)


@pytest.fixture()
def user_id() -> uuid.UUID:
    return uuid7()


def _headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-ID": str(user_id)}


def _seed_navigation(session: Session, user_id: uuid.UUID, document: dict[str, object]) -> None:
    session.add(
        UserPreference(
            user_id=user_id,
            preference_key=NAVIGATION_PREFERENCE_KEY,
            preference_value=document,
            created_by=user_id,
            modified_by=user_id,
        )
    )
    session.commit()


def _pin_entry(pin_key: str, view: ViewRecord, group: str | None = None) -> dict[str, object]:
    return {
        "pinKey": pin_key,
        "panelKey": view.panel_key,
        "viewKey": view.view_key,
        "label": view.name,
        "group": group,
    }


# --- The header, both window kinds (REQ-009 / REQ-012) --------------------------


def test_shell_serves_one_header_definition_for_both_window_kinds(
    client: TestClient, user_id: uuid.UUID
) -> None:
    response = client.get("/shell", headers=_headers(user_id))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    main_window, pop_out = data["mainWindow"], data["popOut"]
    # The main window hosts panels; a pop-out is a record window (WTK-021).
    assert main_window["left"] == ["identity", "navigation"]
    assert main_window["hasNavigation"] is True
    assert pop_out["left"] == ["identity"]
    assert pop_out["hasNavigation"] is False
    # Right side and account menu come from the home frame by reference.
    for header in (main_window, pop_out):
        assert header["right"] == list(HOME_FRAME.header_right)
        assert [i["key"] for i in header["accountMenu"]] == [
            i.key for i in HOME_FRAME.account_menu
        ]
        assert header["quickOpenShortcut"] == "Ctrl+K"
    assert data["homePanelKey"] == "home"


def test_shell_requires_the_user_header(client: TestClient) -> None:
    assert client.get("/shell").status_code == 422


# --- Navigation rendering (REQ-010 / REQ-015) -----------------------------------


def test_navigation_defaults_to_empty_tabs_without_a_stored_profile(
    client: TestClient, user_id: uuid.UUID
) -> None:
    data = client.get("/shell", headers=_headers(user_id)).json()["data"]
    navigation = data["navigation"]
    assert navigation["presentation"] == "tabs"
    assert set(navigation["presentations"]) == {"tabs", "sideMenu", "groupTree"}
    assert navigation["groups"] == [{"label": None, "items": []}]


def test_navigation_renders_stored_pins_marking_broken_ones(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    _seed_navigation(
        session,
        user_id,
        {
            "presentation": "sideMenu",
            "pins": [_pin_entry("p1", ACTIVE_VIEW), _pin_entry("p2", REMOVED_VIEW)],
        },
    )
    navigation = client.get("/shell", headers=_headers(user_id)).json()["data"]["navigation"]
    assert navigation["presentation"] == "sideMenu"
    (group,) = navigation["groups"]
    assert [(i["pinKey"], i["isBroken"]) for i in group["items"]] == [
        ("p1", False),
        ("p2", True),
    ]


def test_navigation_rides_the_preference_pair(client: TestClient, user_id: uuid.UUID) -> None:
    # The settings integration: saving the document through PUT /preferences
    # (REQ-060, the one persistence seam) is what GET /shell renders.
    document = {
        "presentation": "groupTree",
        "pins": [
            _pin_entry("p1", ACTIVE_VIEW, group="Mentoring"),
            _pin_entry("p2", ADMIN_VIEW),
        ],
    }
    put = client.put(
        f"/preferences/{NAVIGATION_PREFERENCE_KEY}",
        headers=_headers(user_id),
        json={"preferenceValue": document},
    )
    assert put.status_code == 200, put.text
    navigation = client.get("/shell", headers=_headers(user_id)).json()["data"]["navigation"]
    assert navigation["presentation"] == "groupTree"
    assert [g["label"] for g in navigation["groups"]] == ["Mentoring", None]
    # The admin pin is broken (no grant on ds.admin) but never dropped.
    (admin_item,) = navigation["groups"][1]["items"]
    assert admin_item["pinKey"] == "p2"
    assert admin_item["isBroken"] is True


# --- The quick-open palette (REQ-009) --------------------------------------------


def test_quick_open_lists_only_reachable_destinations(
    client: TestClient, user_id: uuid.UUID
) -> None:
    data = client.get("/shell/quick-open", headers=_headers(user_id)).json()["data"]
    # Panels first then views, alphabetical; the ungranted admin panel, its
    # view, and the soft-deleted view are not destinations at all.
    assert [(e["kind"], e["label"]) for e in data["entries"]] == [
        ("panel", "Home"),
        ("panel", "Mentors"),
        ("view", "Active Mentors"),
    ]


def test_quick_open_ranks_prefix_matches_first(client: TestClient, user_id: uuid.UUID) -> None:
    # "men" prefixes "Mentors" and only appears inside "Active Mentors" —
    # the prefix band outranks the alphabetical panels-then-views order.
    response = client.get("/shell/quick-open?q=men", headers=_headers(user_id))
    data = response.json()["data"]
    assert [e["label"] for e in data["entries"]] == ["Mentors", "Active Mentors"]
    assert response.json()["meta"]["totalCount"] == 2


# --- Opening a pin (REQ-010) and the broken-pin dialog (REQ-015) ------------------


def test_opening_a_healthy_pin_answers_its_panel_and_view(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    _seed_navigation(
        session, user_id, {"presentation": "tabs", "pins": [_pin_entry("p1", ACTIVE_VIEW)]}
    )
    data = client.post("/shell/navigation/pins/p1/open", headers=_headers(user_id)).json()[
        "data"
    ]
    assert data["opened"] == {"panelKey": "mentors", "viewKey": "views.activeMentors"}
    assert data["dialog"] is None


def test_opening_a_broken_pin_answers_the_explanation_dialog(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    _seed_navigation(
        session, user_id, {"presentation": "tabs", "pins": [_pin_entry("p2", REMOVED_VIEW)]}
    )
    data = client.post("/shell/navigation/pins/p2/open", headers=_headers(user_id)).json()[
        "data"
    ]
    assert data["opened"] is None
    dialog = data["dialog"]
    assert dialog["reason"] == "viewRemoved"
    assert dialog["choices"] == ["removePin", "chooseDifferentView"]
    # The educate message names what was removed and by whom (REQ-015).
    assert "Retired Mentors" in dialog["message"]["why"]
    assert "Dana Admin" in dialog["message"]["why"]


def test_opening_an_unknown_pin_is_404(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    response = client.post("/shell/navigation/pins/gone/open", headers=_headers(user_id))
    assert response.status_code == 404
    body = response.json()
    assert body["data"] is None
    assert body["errors"][0]["code"] == "notFound"


# --- The notification bell (REQ-014, WTK-193) ------------------------------------


def _bell_user(session: Session, username: str = "bell-mentor") -> uuid.UUID:
    user = AppUser(crm_user_id=f"crm-{username}", username=username)
    session.add(user)
    session.flush()
    return user.user_id


def _bell_entry(
    session: Session, user_id: uuid.UUID, message: str, **overrides: object
) -> Notification:
    values: dict[str, object] = {
        "notification_type": "jobCompleted",
        "notification_message": message,
        **overrides,
    }
    entry = Notification(user_id=user_id, **values)
    session.add(entry)
    return entry


def test_bell_lists_only_the_session_users_unread_entries_newest_first(
    client: TestClient, session: Session
) -> None:
    mentor = _bell_user(session)
    neighbor = _bell_user(session, "neighbor")
    older = _bell_entry(session, mentor, "Older export is ready.")
    older.created_at = utcnow() - timedelta(minutes=5)
    newer = _bell_entry(
        session, mentor, "The export could not finish.", notification_type="jobFailed"
    )
    _bell_entry(session, mentor, "Seen already.", read_at=utcnow())
    _bell_entry(session, neighbor, "A different mentor's export.")
    session.commit()

    response = client.get("/shell/bell", headers=_headers(mentor))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["errors"] is None
    assert body["meta"]["unreadCount"] == 2
    entries = body["data"]["entries"]
    # Newest first; read entries and other users' entries never appear.
    assert [e["notificationID"] for e in entries] == [
        str(newer.notification_id),
        str(older.notification_id),
    ]
    assert entries[0]["notificationType"] == "jobFailed"
    assert entries[0]["notificationMessage"] == "The export could not finish."
    assert entries[0]["jobID"] is None
    assert entries[0]["createdAt"]


def test_bell_read_round_trip_stamps_and_clears_the_badge(
    client: TestClient, session: Session
) -> None:
    mentor = _bell_user(session)
    entries = [_bell_entry(session, mentor, f"Export {n} is ready.") for n in range(2)]
    session.commit()

    marked = client.post("/shell/bell/read", headers=_headers(mentor)).json()
    assert marked["errors"] is None
    assert marked["data"] == {"markedRead": 2}
    assert marked["meta"]["unreadCount"] == 0

    # Viewing stamped readAt (REQ-014): the badge clears, the rows stay live.
    after = client.get("/shell/bell", headers=_headers(mentor)).json()
    assert after["meta"]["unreadCount"] == 0 and after["data"]["entries"] == []
    session.expire_all()
    assert all(e.read_at is not None and e.deleted_at is None for e in entries)
    assert all(e.modified_by == mentor for e in entries)

    # Idempotent: a second view finds nothing unread.
    again = client.post("/shell/bell/read", headers=_headers(mentor)).json()
    assert again["data"] == {"markedRead": 0}


def test_bell_requires_the_user_header(client: TestClient) -> None:
    assert client.get("/shell/bell").status_code == 422
    assert client.post("/shell/bell/read").status_code == 422
