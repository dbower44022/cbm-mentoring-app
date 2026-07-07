"""The D9 identity seam (FND-909): the SERVER resolves who is acting.

``mentorapp.api.deps.get_current_user_id`` resolves the acting user from the
opaque ``X-Session-Reference`` on every authenticated call — the
client-claimed ``X-User-ID`` header is gone. These tests pin the observed
defect closed and the refusal contract exact:

- A stale or unknown session reference (the rebuilt-database case that used
  to reach ``/home`` and crash on a SQLite foreign-key IntegrityError)
  answers the structured ``unauthenticated`` 401 — never a 500.
- An expired-but-known session answers the ``reauthRequired`` 401 the
  envelope client holds requests on (in-place re-auth).
- A valid session acts as ITS user, whatever identity a header claims.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from mentorapp.access import (
    InMemorySessionStore,
    SessionManagement,
    VerifiedIdentity,
)
from mentorapp.api.deps import get_session, get_session_management
from mentorapp.api.routers.home import get_home_catalog, get_message_center
from mentorapp.crm.auth import CrmUserCredential
from mentorapp.main import create_app
from mentorapp.storage import uuid7
from mentorapp.ui.home_panel import AdminMessage, MessageCenter


class Clock:
    """Controllable now() so expiry is a test decision, not a sleep."""

    def __init__(self) -> None:
        self.current = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class EmptyCatalog:
    """No panels, no views — composition is not what these tests exercise."""

    def accessible_panel_keys(self, user_id: uuid.UUID) -> Sequence[str]:
        return ()

    def available_view_keys(self, user_id: uuid.UUID) -> Collection[str]:
        return frozenset()


class RecordingCenter(MessageCenter):
    """The reference center, remembering which user each home view acted as."""

    def __init__(self) -> None:
        super().__init__()
        self.viewed_as: list[str] = []

    def view_home(self, user_id: str, now: datetime) -> tuple[AdminMessage, ...]:
        self.viewed_as.append(user_id)
        return super().view_home(user_id, now)


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture()
def sessions(store: InMemorySessionStore, clock: Clock) -> SessionManagement:
    return SessionManagement(store, now=clock)


@pytest.fixture()
def center() -> RecordingCenter:
    return RecordingCenter()


@pytest.fixture()
def client(
    session: Session, sessions: SessionManagement, center: RecordingCenter
) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_session_management] = lambda: sessions
    app.dependency_overrides[get_home_catalog] = EmptyCatalog
    app.dependency_overrides[get_message_center] = lambda: center
    return TestClient(app)


def _establish(sessions: SessionManagement, user_id: uuid.UUID) -> str:
    reference, _record = sessions.establish(
        VerifiedIdentity(
            user_id=user_id,
            role_names=frozenset({"Mentor"}),
            crm_credential=CrmUserCredential(username="mentor", secret="espo-token"),
        )
    )
    return reference


def _codes(body: dict[str, list[dict[str, str]]]) -> set[str]:
    errors = body["errors"]
    assert errors, "a refusal must carry at least one structured error"
    return {entry["code"] for entry in errors}


# --- The FK-crash regression: stale identity is a refusal, never a 500 ----------


def test_stale_reference_from_a_rebuilt_database_answers_401_on_home(
    client: TestClient, sessions: SessionManagement, store: InMemorySessionStore
) -> None:
    # The observed D9 defect shape: the browser holds a reference minted
    # before the database was rebuilt. The session store no longer knows it —
    # the answer is the structured signed-out 401, not a foreign-key crash.
    reference = _establish(sessions, uuid7())
    store.records.clear()  # the rebuild

    response = client.get("/home", headers={"X-Session-Reference": reference})

    assert response.status_code == 401
    assert _codes(response.json()) == {"unauthenticated"}


def test_garbage_reference_answers_401_on_home(client: TestClient) -> None:
    response = client.get("/home", headers={"X-Session-Reference": "not-a-reference"})
    assert response.status_code == 401
    assert _codes(response.json()) == {"unauthenticated"}


def test_missing_reference_answers_401_signed_out(client: TestClient) -> None:
    # No session and a dead session are one client outcome: sign in.
    response = client.get("/home")
    assert response.status_code == 401
    assert _codes(response.json()) == {"unauthenticated"}


def test_stale_reference_answers_401_on_a_grid_read(
    client: TestClient, sessions: SessionManagement, store: InMemorySessionStore
) -> None:
    # Identity resolves before any grid lookup, so no grid fixture is needed:
    # the read surface refuses exactly like /home.
    reference = _establish(sessions, uuid7())
    store.records.clear()

    response = client.get("/grids/engagements/rows", headers={"X-Session-Reference": reference})

    assert response.status_code == 401
    assert _codes(response.json()) == {"unauthenticated"}


# --- Expiry stays revivable: the reauthRequired challenge -----------------------


def test_expired_known_session_answers_the_reauth_shape(
    client: TestClient, sessions: SessionManagement, clock: Clock
) -> None:
    reference = _establish(sessions, uuid7())
    clock.advance(timedelta(minutes=31))  # past the 30-minute idle timeout

    response = client.get("/home", headers={"X-Session-Reference": reference})

    assert response.status_code == 401
    assert _codes(response.json()) == {"reauthRequired"}


# --- The closed hole: a client-claimed identity header changes nothing ----------


def test_the_session_user_acts_even_when_a_header_claims_someone_else(
    client: TestClient, sessions: SessionManagement, center: RecordingCenter
) -> None:
    session_user = uuid7()
    claimed_user = uuid7()
    reference = _establish(sessions, session_user)

    response = client.get(
        "/home",
        headers={
            "X-Session-Reference": reference,
            # The pre-D9 trusted header: it must be dead weight now.
            "X-User-ID": str(claimed_user),
        },
    )

    assert response.status_code == 200
    assert center.viewed_as == [str(session_user)]
