"""Stored admin messaging over the API: persistence, receipts, admin CRUD (WTK-192, REQ-011).

Runs the real production binding (``install_home_wiring`` →
:class:`~mentorapp.api.messages.StoredMessageCenter` over the test session),
so these tests prove the tables back every MessageCenter reference behavior:
auto-read on view, explicit-only acknowledgment, the urgent banner clearing
on READ, and an acknowledgment audit that survives message expiration and
answers the admin report after the message has left Home.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from identity_stub import header_user_id
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.routers.home import get_home_catalog
from mentorapp.main import create_app
from mentorapp.storage import AdminMessageReceipt, AppUser, utcnow, uuid7


class StubCatalog:
    """Fixed permissioned world — messaging is what these tests exercise."""

    def accessible_panel_keys(self, user_id: uuid.UUID) -> Sequence[str]:
        return ("home",)

    def available_view_keys(self, user_id: uuid.UUID) -> frozenset[str]:
        return frozenset()


@pytest.fixture()
def client(session: Session) -> TestClient:
    # get_message_center/get_message_admin stay on the production home wiring
    # deliberately: the stored center IS the subject under test.
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    # The D9 identity seam resolves sessions in production; these are not
    # session-lifecycle tests, so the stub names the acting user directly.
    app.dependency_overrides[get_current_user_id] = header_user_id
    app.dependency_overrides[get_home_catalog] = lambda: StubCatalog()
    return TestClient(app)


def _make_user(session: Session, name: str) -> uuid.UUID:
    user = AppUser(crm_user_id=f"crm-{name}", username=name)
    session.add(user)
    session.commit()
    return user.user_id


@pytest.fixture()
def mentor_id(session: Session) -> uuid.UUID:
    return _make_user(session, "mentor")


@pytest.fixture()
def admin_id(session: Session) -> uuid.UUID:
    return _make_user(session, "admin")


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


# --- Persistence of the reference behaviors -------------------------------------


def test_view_home_reads_persistently_across_requests(
    client: TestClient, session: Session, mentor_id: uuid.UUID, admin_id: uuid.UUID
) -> None:
    _post_message(client, admin_id)
    first = client.get("/home", headers=_headers(mentor_id)).json()
    assert first["meta"]["unreadCount"] == 1
    assert first["data"]["messages"][0]["title"] == "Maintenance window"
    # FND-909 D13: postedBy serves the poster's NAME, never the raw userID.
    assert first["data"]["messages"][0]["postedBy"] == "admin"
    # Auto-read on view survived the request: the receipt is a row, not memory.
    second = client.get("/home", headers=_headers(mentor_id)).json()
    assert second["meta"]["unreadCount"] == 0
    receipt = session.scalars(select(AdminMessageReceipt)).one()
    assert receipt.user_id == mentor_id
    assert receipt.message_read_at is not None
    assert receipt.message_acknowledged_at is None  # viewing never acknowledges


def test_urgent_banner_clears_on_read_not_acknowledgment(
    client: TestClient, mentor_id: uuid.UUID, admin_id: uuid.UUID
) -> None:
    posted = _post_message(client, admin_id, priority="urgent", requiresAcknowledgment=True)
    banner = client.get("/home/banner", headers=_headers(mentor_id)).json()
    assert [m["messageKey"] for m in banner["data"]["messages"]] == [posted["messageKey"]]
    # The banner's open-the-message act reads it — banner gone, ack still open.
    read = client.post(
        f"/home/messages/{posted['messageKey']}/read", headers=_headers(mentor_id)
    )
    assert read.status_code == 200
    assert read.json()["data"]["acknowledged"] is False
    again = client.get("/home/banner", headers=_headers(mentor_id)).json()
    assert again["data"]["messages"] == []
    assert again["meta"]["unreadCount"] == 0


def test_acknowledge_is_explicit_recorded_once_and_guarded(
    client: TestClient, session: Session, mentor_id: uuid.UUID, admin_id: uuid.UUID
) -> None:
    posted = _post_message(client, admin_id, requiresAcknowledgment=True)
    key = posted["messageKey"]
    assert (
        client.post(f"/home/messages/{key}/acknowledge", headers=_headers(mentor_id)).json()[
            "data"
        ]["acknowledged"]
        is True
    )
    receipt = session.scalars(select(AdminMessageReceipt)).one()
    first_stamp = receipt.message_acknowledged_at
    assert first_stamp is not None
    assert receipt.message_read_at is not None  # acknowledging implies read
    # A repeat click never rewrites the first consent's timestamp.
    client.post(f"/home/messages/{key}/acknowledge", headers=_headers(mentor_id))
    session.expire_all()
    assert session.scalars(select(AdminMessageReceipt)).one().message_acknowledged_at == (
        first_stamp
    )
    # A message that never asked refuses loudly (caller bug, not user state).
    plain = _post_message(client, admin_id)
    refused = client.post(
        f"/home/messages/{plain['messageKey']}/acknowledge", headers=_headers(mentor_id)
    )
    assert refused.status_code == 422
    assert refused.json()["errors"][0]["code"] == "acknowledgmentNotRequested"
    # Unknown and malformed keys answer identically.
    assert (
        client.post(
            f"/home/messages/{uuid7()}/acknowledge", headers=_headers(mentor_id)
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/home/messages/not-a-key/acknowledge", headers=_headers(mentor_id)
        ).status_code
        == 404
    )


def test_acknowledgment_audit_survives_expiration(
    client: TestClient, mentor_id: uuid.UUID, admin_id: uuid.UUID
) -> None:
    other = str(uuid7())
    expired = _post_message(
        client,
        admin_id,
        requiresAcknowledgment=True,
        expiresAt=(utcnow() - timedelta(minutes=1)).isoformat(),
    )
    key = expired["messageKey"]
    # Expired: gone from Home and refusing the read/render path...
    assert client.get("/home", headers=_headers(mentor_id)).json()["data"]["messages"] == []
    assert (
        client.post(f"/home/messages/{key}/read", headers=_headers(mentor_id)).status_code
        == 404
    )
    # ...but acknowledging still lands (a banner opened just before expiry),
    assert (
        client.post(
            f"/home/messages/{key}/acknowledge", headers=_headers(mentor_id)
        ).status_code
        == 200
    )
    # and the admin report reads the audit after the message is gone.
    report = client.get(
        f"/home/messages/{key}/acknowledgments",
        headers=_headers(admin_id),
        params=[("userId", str(mentor_id)), ("userId", other)],
    ).json()["data"]
    assert report["acknowledged"] == [str(mentor_id)]
    assert report["outstanding"] == [other]


# --- Admin CRUD (WTK-192) ---------------------------------------------------------


def test_admin_list_includes_expired_messages_newest_first(
    client: TestClient, admin_id: uuid.UUID
) -> None:
    expired = _post_message(
        client, admin_id, title="Old", expiresAt=(utcnow() - timedelta(days=1)).isoformat()
    )
    live = _post_message(client, admin_id, title="Current")
    listed = client.get("/home/messages", headers=_headers(admin_id)).json()["data"]["messages"]
    assert [m["messageKey"] for m in listed] == [live["messageKey"], expired["messageKey"]]
    assert all(m["rowVersion"] == 1 for m in listed)


def test_patch_edits_only_sent_fields_under_row_version(
    client: TestClient, mentor_id: uuid.UUID, admin_id: uuid.UUID
) -> None:
    posted = _post_message(
        client, admin_id, expiresAt=(utcnow() - timedelta(minutes=1)).isoformat()
    )
    key = posted["messageKey"]
    # An expired message stays editable: clearing expiresAt brings it back.
    patched = client.patch(
        f"/home/messages/{key}",
        headers=_headers(admin_id),
        json={"rowVersion": 1, "title": "Rescheduled", "expiresAt": None},
    )
    assert patched.status_code == 200, patched.text
    record = patched.json()["data"]
    assert record["title"] == "Rescheduled"
    assert record["body"] == "Saturday 06:00."  # unsent field untouched
    assert record["expiresAt"] is None
    assert record["rowVersion"] == 2
    home = client.get("/home", headers=_headers(mentor_id)).json()["data"]["messages"]
    assert [m["title"] for m in home] == ["Rescheduled"]
    # Stale rowVersion → 409 with the CURRENT record in data (DB-S4/S12).
    stale = client.patch(
        f"/home/messages/{key}",
        headers=_headers(admin_id),
        json={"rowVersion": 1, "title": "Lost update"},
    )
    assert stale.status_code == 409
    assert stale.json()["errors"][0]["code"] == "staleRowVersion"
    assert stale.json()["data"]["title"] == "Rescheduled"
    assert stale.json()["data"]["rowVersion"] == 2
    assert (
        client.patch(
            f"/home/messages/{uuid7()}",
            headers=_headers(admin_id),
            json={"rowVersion": 1, "title": "x"},
        ).status_code
        == 404
    )


def test_delete_is_soft_and_keeps_the_receipt_audit(
    client: TestClient, session: Session, mentor_id: uuid.UUID, admin_id: uuid.UUID
) -> None:
    posted = _post_message(client, admin_id, requiresAcknowledgment=True)
    key = posted["messageKey"]
    client.post(f"/home/messages/{key}/acknowledge", headers=_headers(mentor_id))
    deleted = client.delete(f"/home/messages/{key}", headers=_headers(admin_id))
    assert deleted.status_code == 200
    # Gone from every surface: the admin list, Home, and the render path.
    assert (
        client.get("/home/messages", headers=_headers(admin_id)).json()["data"]["messages"]
        == []
    )
    assert client.get("/home", headers=_headers(mentor_id)).json()["data"]["messages"] == []
    assert client.delete(f"/home/messages/{key}", headers=_headers(admin_id)).status_code == 404
    # Soft delete (DB-S3): the row and its acknowledgment audit remain.
    receipt = session.scalars(select(AdminMessageReceipt)).one()
    assert receipt.deleted_at is None
    assert receipt.message_acknowledged_at is not None
