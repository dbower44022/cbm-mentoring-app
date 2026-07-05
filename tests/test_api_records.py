"""``/records/{type}/{id}/preview`` — the record-window content surface (REQ-012)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Mapped, Session, mapped_column

from mentorapp.api.deps import get_session
from mentorapp.api.routers.records import get_record_catalog
from mentorapp.main import create_app
from mentorapp.storage import BaseEntity, entity_key, utcnow, uuid7


class PreviewMentor(BaseEntity):
    __tablename__ = "PreviewMentor"

    preview_mentor_id: Mapped[uuid.UUID] = entity_key("previewMentorID")
    mentor_name: Mapped[str] = mapped_column("mentorName", nullable=False)


@dataclass(frozen=True)
class StubCatalog:
    """The entity catalog, stubbed: one known wire name."""

    entities: Mapping[str, type[Any]] = field(default_factory=lambda: {"mentor": PreviewMentor})

    def entity_class(self, entity_type: str) -> type[Any] | None:
        return self.entities.get(entity_type)


@pytest.fixture()
def client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_record_catalog] = lambda: StubCatalog()
    return TestClient(app)


@pytest.fixture()
def user_id() -> uuid.UUID:
    return uuid7()


def _headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-ID": str(user_id)}


def _mentor(session: Session, **overrides: Any) -> PreviewMentor:
    record = PreviewMentor(mentor_name="Ada Lovelace", **overrides)
    session.add(record)
    session.flush()
    return record


def _get_preview(
    client: TestClient, user_id: uuid.UUID, record_id: uuid.UUID, entity_type: str = "mentor"
) -> Any:
    return client.get(f"/records/{entity_type}/{record_id}/preview", headers=_headers(user_id))


# --- The read-optimized content (REQ-012) ------------------------------------------


def test_preview_serves_the_pane_declaration_and_flat_record(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    record = _mentor(session)
    response = _get_preview(client, user_id, record.preview_mentor_id)
    assert response.status_code == 200
    body = response.json()
    assert body["errors"] is None
    pane = body["data"]["pane"]
    # The WTK-021 declarations, verbatim: read mode, zero edit controls, the
    # two edit paths, right dock.
    assert pane["readOptimized"] is True
    assert pane["editControls"] is False
    assert pane["editPaths"] == ["editAction", "perFieldDoubleClick"]
    assert pane["dockPosition"] == "right"
    served = body["data"]["record"]
    assert served["previewMentorID"] == str(record.preview_mentor_id)
    assert served["mentorName"] == "Ada Lovelace"
    # rowVersion rides along so any read can lead straight to an edit (DB-S4).
    assert served["rowVersion"] == 1
    assert body["data"]["notice"] is None


def test_custom_attributes_ride_flat_in_the_record(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    record = _mentor(session, custom_attributes={"favoriteColor": "teal"})
    served = _get_preview(client, user_id, record.preview_mentor_id).json()["data"]["record"]
    # DB-R3: custom attributes are indistinguishable from built-ins; the raw
    # bag never reaches the wire.
    assert served["favoriteColor"] == "teal"
    assert "customAttributes" not in served


def test_pop_out_frame_is_a_real_window_with_header_minus_navigation(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    record = _mentor(session)
    body = _get_preview(client, user_id, record.preview_mentor_id).json()
    frame = body["data"]["popOutFrame"]
    assert frame["kind"] == "browserWindow"
    assert frame["hasNavigation"] is False
    assert frame["headerRight"] == ["notificationBell", "help", "accountMenu"]


# --- The three not-a-record answers -------------------------------------------------


def test_unknown_entity_type_educates_with_a_422(
    client: TestClient, user_id: uuid.UUID
) -> None:
    response = _get_preview(client, user_id, uuid7(), entity_type="gadget")
    assert response.status_code == 422
    (error,) = response.json()["errors"]
    assert error["fieldName"] == "entityType"
    assert error["code"] == "unknownEntityType"
    assert "'gadget'" in error["message"]


def test_unknown_record_is_a_404(client: TestClient, user_id: uuid.UUID) -> None:
    missing = uuid7()
    response = _get_preview(client, user_id, missing)
    assert response.status_code == 404
    (error,) = response.json()["errors"]
    assert error["code"] == "notFound"
    assert str(missing) in error["message"]


def test_soft_deleted_record_still_answers_with_an_honest_notice(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    record = _mentor(session, deleted_at=utcnow(), deleted_by=uuid7())
    response = _get_preview(client, user_id, record.preview_mentor_id)
    # A pop-out is pinned to its record: removal explains itself, never 404s
    # a window the user is looking at.
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["record"]["mentorName"] == "Ada Lovelace"
    notice = data["notice"]
    assert notice["whatHappened"] == "This record has been removed."
    # Honest soft-delete wording: kept and restorable, not destroyed.
    assert "kept, not destroyed" in notice["why"]
    assert "administrator can restore" in notice["whatNext"]


# --- The seams fail the standard ways ------------------------------------------------


def test_missing_user_header_is_the_standard_422(client: TestClient, session: Session) -> None:
    record = _mentor(session)
    response = client.get(f"/records/mentor/{record.preview_mentor_id}/preview")
    assert response.status_code == 422


def test_unwired_catalog_fails_loudly(session: Session, user_id: uuid.UUID) -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)
    with pytest.raises(RuntimeError, match="record catalog provider is not wired"):
        _get_preview(client, user_id, uuid7())
