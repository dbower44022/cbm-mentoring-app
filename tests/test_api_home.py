"""``/home`` — frame, Areas, dashlets, admin messaging over the envelope (REQ-003, REQ-011)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from identity_stub import header_user_id
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.routers.home import get_home_catalog, get_message_center
from mentorapp.main import create_app
from mentorapp.storage import UserPreference, utcnow, uuid7
from mentorapp.ui.home_panel import HOME_DASHLETS_PREFERENCE_KEY, MessageCenter


@dataclass(frozen=True)
class StubCatalog:
    """The grant-derived catalog, stubbed: fixed panels and dashlet views."""

    panel_keys: tuple[str, ...] = ("home", "mentors", "sessions")
    view_keys: frozenset[str] = field(
        default_factory=lambda: frozenset({"views.sessionsThisWeek"})
    )

    def accessible_panel_keys(self, user_id: uuid.UUID) -> Sequence[str]:
        return self.panel_keys

    def available_view_keys(self, user_id: uuid.UUID) -> frozenset[str]:
        return self.view_keys


@pytest.fixture()
def center() -> MessageCenter:
    return MessageCenter()


@pytest.fixture()
def client(session: Session, center: MessageCenter) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    # The D9 identity seam resolves sessions in production; these are not
    # session-lifecycle tests, so the stub names the acting user directly.
    app.dependency_overrides[get_current_user_id] = header_user_id
    app.dependency_overrides[get_home_catalog] = lambda: StubCatalog()
    app.dependency_overrides[get_message_center] = lambda: center
    return TestClient(app)


@pytest.fixture()
def user_id() -> uuid.UUID:
    return uuid7()


@pytest.fixture()
def admin_id() -> uuid.UUID:
    return uuid7()


def _headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-ID": str(user_id)}


def _post_message(
    client: TestClient, admin_id: uuid.UUID, **overrides: object
) -> dict[str, object]:
    body: dict[str, object] = {"title": "Maintenance window", "body": "Saturday 06:00."}
    body.update(overrides)
    response = client.post("/home/messages", headers=_headers(admin_id), json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]


# --- The frame and Areas rail (REQ-003) -----------------------------------------


def test_home_serves_the_frame_with_help_last_and_areas_minus_home(
    client: TestClient, user_id: uuid.UUID
) -> None:
    body = client.get("/home", headers=_headers(user_id)).json()
    frame = body["data"]["frame"]
    assert frame["logoZone"] == "upperLeft"
    assert frame["identityZone"] == "upperRight"
    assert frame["areasZone"] == "leftEdge"
    assert frame["headerRight"] == ["notificationBell", "help", "accountMenu"]
    # The app-wide rule: the last item in EVERY menu is Help.
    assert frame["accountMenu"][-1]["key"] == "help"
    assert body["data"]["areas"] == ["mentors", "sessions"]


# --- Dashlets (REQ-011) -----------------------------------------------------------


def test_dashlets_lead_with_messages_and_keep_broken_ones_visible(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    session.add(
        UserPreference(
            user_id=user_id,
            preference_key=HOME_DASHLETS_PREFERENCE_KEY,
            preference_value={
                "dashlets": [
                    {"viewKey": "views.sessionsThisWeek", "title": "This week"},
                    {"viewKey": "views.retired", "title": "Retired view"},
                ]
            },
        )
    )
    session.commit()
    dashlets = client.get("/home", headers=_headers(user_id)).json()["data"]["dashlets"]
    assert dashlets[0]["viewKey"] == "home.messages"
    assert dashlets[1] == {
        "viewKey": "views.sessionsThisWeek",
        "title": "This week",
        "notice": None,
    }
    # Broken-pin rule applied to dashlets: visible, with the educate voice.
    assert dashlets[2]["viewKey"] == "views.retired"
    assert dashlets[2]["notice"]["whatHappened"].startswith("The dashlet 'Retired view'")
    assert "what_next" not in dashlets[2]["notice"]


def test_dashlets_fall_back_to_the_org_default_arrangement(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    session.add(
        UserPreference(
            preference_key=HOME_DASHLETS_PREFERENCE_KEY,
            preference_value={
                "dashlets": [{"viewKey": "views.sessionsThisWeek", "title": "This week"}]
            },
        )
    )
    session.commit()
    dashlets = client.get("/home", headers=_headers(user_id)).json()["data"]["dashlets"]
    assert [d["viewKey"] for d in dashlets] == ["home.messages", "views.sessionsThisWeek"]


# --- Messages: read state and auto-read on view (REQ-011) --------------------------


def test_opening_home_reads_its_messages_newest_first(
    client: TestClient, user_id: uuid.UUID, admin_id: uuid.UUID
) -> None:
    _post_message(client, admin_id, title="First")
    _post_message(client, admin_id, title="Second")
    body = client.get("/home", headers=_headers(user_id)).json()
    assert body["meta"]["unreadCount"] == 2
    assert [m["title"] for m in body["data"]["messages"]] == ["Second", "First"]
    assert body["data"]["messages"][0]["postedBy"] == str(admin_id)
    # Auto-read on view: the open above cleared the badge.
    again = client.get("/home", headers=_headers(user_id)).json()
    assert again["meta"]["unreadCount"] == 0


def test_read_state_is_per_user(
    client: TestClient, user_id: uuid.UUID, admin_id: uuid.UUID
) -> None:
    _post_message(client, admin_id)
    client.get("/home", headers=_headers(user_id))
    other = uuid7()
    assert client.get("/home", headers=_headers(other)).json()["meta"]["unreadCount"] == 1


def test_expired_messages_neither_render_nor_count(
    client: TestClient, user_id: uuid.UUID, admin_id: uuid.UUID
) -> None:
    _post_message(client, admin_id, expiresAt=(utcnow() - timedelta(minutes=1)).isoformat())
    body = client.get("/home", headers=_headers(user_id)).json()
    assert body["data"]["messages"] == []
    assert body["meta"]["unreadCount"] == 0


# --- The urgent banner: until READ, never extended by acknowledgment ---------------


def test_urgent_message_banners_until_read_even_with_acknowledgment_outstanding(
    client: TestClient, user_id: uuid.UUID, admin_id: uuid.UUID
) -> None:
    posted = _post_message(client, admin_id, priority="urgent", requiresAcknowledgment=True)
    key = posted["messageKey"]
    banner = client.get("/home/banner", headers=_headers(user_id)).json()
    assert [m["messageKey"] for m in banner["data"]["messages"]] == [key]
    assert banner["meta"]["unreadCount"] == 1

    # The banner's open-the-message act reads exactly this one message...
    read = client.post(f"/home/messages/{key}/read", headers=_headers(user_id))
    assert read.status_code == 200
    assert read.json()["data"]["acknowledged"] is False

    # ...which clears the banner even though acknowledgment is still owed:
    # the banner guarantees delivery, not consent.
    banner = client.get("/home/banner", headers=_headers(user_id)).json()
    assert banner["data"]["messages"] == []
    audit = client.get(
        f"/home/messages/{key}/acknowledgments",
        headers=_headers(admin_id),
        params=[("userId", str(user_id))],
    ).json()
    assert audit["data"]["outstanding"] == [str(user_id)]


def test_normal_messages_never_banner(
    client: TestClient, user_id: uuid.UUID, admin_id: uuid.UUID
) -> None:
    _post_message(client, admin_id)
    banner = client.get("/home/banner", headers=_headers(user_id)).json()
    assert banner["data"]["messages"] == []
    assert banner["meta"]["unreadCount"] == 1


# --- Acknowledgment: explicit only, audited (REQ-011) -------------------------------


def test_acknowledge_records_the_click_and_the_audit_splits_the_roster(
    client: TestClient, user_id: uuid.UUID, admin_id: uuid.UUID
) -> None:
    key = _post_message(client, admin_id, requiresAcknowledgment=True)["messageKey"]
    response = client.post(f"/home/messages/{key}/acknowledge", headers=_headers(user_id))
    assert response.status_code == 200
    assert response.json()["data"] == {"messageKey": key, "acknowledged": True}

    other = uuid7()
    audit = client.get(
        f"/home/messages/{key}/acknowledgments",
        headers=_headers(admin_id),
        params=[("userId", str(user_id)), ("userId", str(other))],
    ).json()
    assert audit["data"]["acknowledged"] == [str(user_id)]
    assert audit["data"]["outstanding"] == [str(other)]
    # Acknowledging implies having read — the urgent banner can never outlive it.
    home = client.get("/home", headers=_headers(user_id)).json()
    assert home["meta"]["unreadCount"] == 0
    assert home["data"]["messages"][0]["acknowledged"] is True


def test_acknowledge_refuses_a_message_that_never_asked(
    client: TestClient, user_id: uuid.UUID, admin_id: uuid.UUID
) -> None:
    key = _post_message(client, admin_id)["messageKey"]
    response = client.post(f"/home/messages/{key}/acknowledge", headers=_headers(user_id))
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "acknowledgmentNotRequested"


def test_unknown_message_is_404_in_envelope(client: TestClient, user_id: uuid.UUID) -> None:
    for response in (
        client.post("/home/messages/nope/read", headers=_headers(user_id)),
        client.post("/home/messages/nope/acknowledge", headers=_headers(user_id)),
        client.get("/home/messages/nope/acknowledgments", headers=_headers(user_id)),
    ):
        assert response.status_code == 404
        assert response.json()["errors"][0]["code"] == "notFound"


# --- Publishing ---------------------------------------------------------------------


def test_post_message_rejects_an_empty_title(client: TestClient, admin_id: uuid.UUID) -> None:
    response = client.post(
        "/home/messages", headers=_headers(admin_id), json={"title": "", "body": "x"}
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["fieldName"] == "title"


# --- The provider seams fail loudly until wired --------------------------------------


def test_unwired_providers_refuse_clearly() -> None:
    with pytest.raises(RuntimeError, match="home catalog provider is not wired"):
        get_home_catalog()
    with pytest.raises(RuntimeError, match="message center provider is not wired"):
        get_message_center()
