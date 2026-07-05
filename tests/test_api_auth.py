"""``POST /auth/*`` — the authentication surface contract (WTK-004, REQ-005/007/008).

The design-gate assertions: opaque references only, and every refusal
generic — the indistinguishability tests compare whole response bodies, not
just status codes, because a differing message IS the enumeration channel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mentorapp.access import (
    InMemorySessionStore,
    InMemoryTokenActionStore,
    SessionManagement,
    SessionNotFoundError,
    TokenActionService,
    VerifiedIdentity,
)
from mentorapp.api.routers.auth import (
    get_credential_verifier,
    get_forgot_password_flow,
    get_session_management,
    get_token_actions,
)
from mentorapp.main import create_app
from mentorapp.storage import uuid7


class Clock:
    """Controllable now() so expiry is a test decision, not a sleep."""

    def __init__(self) -> None:
        self.current = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


@dataclass
class FakeVerifier:
    """CredentialVerifier double: a fixed login table; None on any failure."""

    accounts: dict[str, tuple[str, VerifiedIdentity]] = field(default_factory=dict)

    def verify(self, login_name: str, password: str) -> VerifiedIdentity | None:
        entry = self.accounts.get(login_name)
        if entry is None or entry[0] != password:
            return None
        return entry[1]


@dataclass
class RecordingForgotPasswordFlow:
    """ForgotPasswordFlow double that records every initiation."""

    initiated: list[str] = field(default_factory=list)

    def initiate(self, login_name: str) -> None:
        self.initiated.append(login_name)


MENTOR = VerifiedIdentity(user_id=uuid7(), role_names=frozenset({"mentor"}))
OTHER = VerifiedIdentity(user_id=uuid7(), role_names=frozenset({"mentor"}))
MENTOR_LOGIN = "mentor@cbm.org"
MENTOR_PASSWORD = "correct-horse"


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def sessions(clock: Clock) -> SessionManagement:
    return SessionManagement(InMemorySessionStore(), now=clock)


@pytest.fixture()
def tokens(clock: Clock) -> TokenActionService:
    return TokenActionService(
        InMemoryTokenActionStore(), signing_key=b"test-signing-key", now=clock
    )


@pytest.fixture()
def forgot_flow() -> RecordingForgotPasswordFlow:
    return RecordingForgotPasswordFlow()


@pytest.fixture()
def client(
    sessions: SessionManagement,
    tokens: TokenActionService,
    forgot_flow: RecordingForgotPasswordFlow,
) -> TestClient:
    verifier = FakeVerifier(
        accounts={
            MENTOR_LOGIN: (MENTOR_PASSWORD, MENTOR),
            "other@cbm.org": ("battery-staple", OTHER),
        }
    )
    app = create_app()
    app.dependency_overrides[get_session_management] = lambda: sessions
    app.dependency_overrides[get_credential_verifier] = lambda: verifier
    app.dependency_overrides[get_token_actions] = lambda: tokens
    app.dependency_overrides[get_forgot_password_flow] = lambda: forgot_flow
    return TestClient(app)


def _login(client: TestClient, login: str = MENTOR_LOGIN, password: str = MENTOR_PASSWORD):
    return client.post("/auth/login", json={"loginName": login, "password": password})


def _reauth(client: TestClient, reference: str, login: str, password: str):
    return client.post(
        "/auth/reauth",
        json={"sessionReference": reference, "loginName": login, "password": password},
    )


def test_login_returns_the_opaque_reference_and_the_callers_identity(
    client: TestClient,
) -> None:
    response = _login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["errors"] is None
    data = body["data"]
    # The whole payload: one opaque reference plus the caller's own identity —
    # no expiry, state, or any other server-side session detail.
    assert set(data) == {"sessionReference", "userID", "roleNames"}
    assert data["userID"] == str(MENTOR.user_id)
    assert data["roleNames"] == ["mentor"]
    assert MENTOR_LOGIN not in data["sessionReference"]


def test_login_refusals_are_indistinguishable(client: TestClient) -> None:
    unknown_user = _login(client, login="nobody@cbm.org")
    wrong_password = _login(client, password="wrong")
    assert unknown_user.status_code == wrong_password.status_code == 401
    assert unknown_user.json() == wrong_password.json()
    assert unknown_user.json()["errors"][0]["code"] == "invalidCredentials"


def test_logout_ends_the_session_and_is_generic_for_unknown_references(
    client: TestClient, sessions: SessionManagement
) -> None:
    reference = _login(client).json()["data"]["sessionReference"]
    real = client.post("/auth/logout", json={"sessionReference": reference})
    assert real.status_code == 200
    with pytest.raises(SessionNotFoundError):
        # The shared record is ended and the reference resolves nowhere: every
        # window's next request fails closed.
        sessions.resolve(reference)
    fake = client.post("/auth/logout", json={"sessionReference": "not-a-reference"})
    assert fake.status_code == 200
    assert fake.json() == real.json()


def test_reauth_revives_the_same_session_and_rotates_the_reference(
    client: TestClient, clock: Clock, sessions: SessionManagement
) -> None:
    old = _login(client).json()["data"]["sessionReference"]
    clock.advance(timedelta(minutes=31))  # past the default 30-minute idle timeout
    response = _reauth(client, old, MENTOR_LOGIN, MENTOR_PASSWORD)
    assert response.status_code == 200
    new = response.json()["data"]["sessionReference"]
    # Same session identity (the `<sessionID hex>.` prefix), so the one new
    # reference works in every window; the secret rotated, so the old one dies.
    assert new != old
    assert new.split(".")[0] == old.split(".")[0]
    with pytest.raises(SessionNotFoundError):
        sessions.resolve(old)
    assert sessions.resolve(new).session_id.hex == new.split(".")[0]


def test_reauth_refreshes_an_active_session_too(client: TestClient) -> None:
    old = _login(client).json()["data"]["sessionReference"]
    response = _reauth(client, old, MENTOR_LOGIN, MENTOR_PASSWORD)
    assert response.status_code == 200
    assert response.json()["data"]["sessionReference"] != old


def test_reauth_as_a_different_user_reads_exactly_like_a_bad_password(
    client: TestClient, clock: Clock
) -> None:
    reference = _login(client).json()["data"]["sessionReference"]
    clock.advance(timedelta(minutes=31))
    mismatch = _reauth(client, reference, "other@cbm.org", "battery-staple")
    bad_password = _reauth(client, reference, MENTOR_LOGIN, "wrong")
    assert mismatch.status_code == bad_password.status_code == 401
    assert mismatch.json() == bad_password.json()
    assert mismatch.json()["errors"][0]["code"] == "invalidCredentials"


def test_reauth_after_the_grace_window_requires_a_fresh_login(
    client: TestClient, clock: Clock, sessions: SessionManagement
) -> None:
    reference = _login(client).json()["data"]["sessionReference"]
    clock.advance(timedelta(hours=13))  # past the 12-hour absolute lifetime
    with pytest.raises(Exception, match="re-authentication required"):
        sessions.resolve(reference)  # a window's request moves it to reauth-pending
    clock.advance(timedelta(hours=13))  # ...and the 12-hour grace lapses unanswered
    response = _reauth(client, reference, MENTOR_LOGIN, MENTOR_PASSWORD)
    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "unauthenticated"


def test_forgot_password_answers_identically_for_unknown_accounts(
    client: TestClient, forgot_flow: RecordingForgotPasswordFlow
) -> None:
    known = client.post("/auth/forgot-password", json={"loginName": MENTOR_LOGIN})
    unknown = client.post("/auth/forgot-password", json={"loginName": "nobody@cbm.org"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert known.json()["data"] == {"resetRequestAccepted": True}
    # Both reach the seam — deciding whether the account exists is the flow's
    # job, behind the constant response, never the endpoint's.
    assert forgot_flow.initiated == [MENTOR_LOGIN, "nobody@cbm.org"]


def test_redeeming_an_action_token_spends_a_use_and_names_no_identity(
    client: TestClient, clock: Clock, tokens: TokenActionService
) -> None:
    token = tokens.mint(
        user_id=MENTOR.user_id,
        action_name="passwordReset",
        expires_at=clock.current + timedelta(hours=1),
    )
    response = client.post("/auth/actions/passwordReset/redeem", json={"actionToken": token})
    assert response.status_code == 200
    assert response.json()["data"] == {"actionName": "passwordReset", "usesRemaining": 0}
    second = client.post("/auth/actions/passwordReset/redeem", json={"actionToken": token})
    assert second.status_code == 403
    assert second.json()["errors"][0]["code"] == "tokenExhausted"


def test_token_refusals_share_one_generic_message(
    client: TestClient, clock: Clock, tokens: TokenActionService
) -> None:
    token = tokens.mint(
        user_id=MENTOR.user_id,
        action_name="passwordReset",
        expires_at=clock.current + timedelta(hours=1),
    )

    def redeem(action_name: str, action_token: str):
        return client.post(
            f"/auth/actions/{action_name}/redeem", json={"actionToken": action_token}
        )

    wrong_action = redeem("confirmAttendance", token)
    garbage = redeem("passwordReset", "not-a-token")
    clock.advance(timedelta(hours=2))
    expired = redeem("passwordReset", token)
    assert wrong_action.status_code == garbage.status_code == expired.status_code == 403
    # Codes stay precise (the holder proved possession via the signature);
    # the human message is one wording for every refusal.
    assert wrong_action.json()["errors"][0]["code"] == "tokenInvalid"
    assert garbage.json()["errors"][0]["code"] == "tokenInvalid"
    assert expired.json()["errors"][0]["code"] == "tokenExpired"
    messages = {r.json()["errors"][0]["message"] for r in (wrong_action, garbage, expired)}
    assert len(messages) == 1
