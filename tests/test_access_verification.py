"""The wired login seams (WTK-010): composed verifier, recovery flow, full chain.

Covers :class:`CrmCredentialVerifier` (CRM proof → identity bridge →
``VerifiedIdentity``) and :class:`CrmForgotPasswordFlow` at the seam level,
then the whole deployment chain end-to-end: auth endpoints → composed
verifier → :class:`EspoAuthGateway` → :class:`HttpxEspoTransport` → a mock
Espo site — login producing a session, Espo's 401 as ``invalidCredentials``,
and the constant forgot-password answer over Espo's 404.
"""

from __future__ import annotations

from base64 import b64decode

import httpx
import pytest
from fastapi.testclient import TestClient

from mentorapp.access import (
    CrmCredentialVerifier,
    CrmForgotPasswordFlow,
    InMemorySessionStore,
    SessionManagement,
)
from mentorapp.access.identity import InMemoryIdentityBridge
from mentorapp.api.routers.auth import (
    get_credential_verifier,
    get_forgot_password_flow,
    get_session_management,
)
from mentorapp.crm import (
    CredentialsRejectedError,
    CrmUnavailableError,
    CrmUserCredential,
    CrmVerifiedIdentity,
    EspoAuthGateway,
)
from mentorapp.crm.http import HttpxEspoTransport
from mentorapp.main import create_app

MENTOR_IDENTITY = CrmVerifiedIdentity(
    crm_user_id="espo-user-1",
    username="mentor@cbm.org",
    display_name="Mentor One",
    email_address="mentor@cbm.org",
    role_names=frozenset({"Mentors"}),
    credential=CrmUserCredential(username="mentor@cbm.org", secret="espo-token"),
)


class FakeVerification:
    """CredentialVerification double: one account, typed outcomes otherwise."""

    def __init__(self, outcome: CrmVerifiedIdentity | Exception) -> None:
        self._outcome = outcome

    def verify(self, username: str, password: str) -> CrmVerifiedIdentity:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class RecordingForgotPassword:
    """ForgotPassword double recording what was forwarded."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def request_password_reset(self, username: str, email_address: str) -> None:
        self.requests.append((username, email_address))


def test_the_composed_verifier_resolves_crm_proof_into_the_app_identity() -> None:
    bridge = InMemoryIdentityBridge()
    verifier = CrmCredentialVerifier(FakeVerification(MENTOR_IDENTITY), bridge)

    first = verifier.verify("mentor@cbm.org", "correct horse")
    second = verifier.verify("mentor@cbm.org", "correct horse")

    # Find-or-provision: the same CRM user maps to the same app user on every
    # login, and the CRM-issued credential rides through for CrmAccess.
    assert first.user_id == second.user_id
    assert first.role_names == frozenset({"Mentors"})
    assert first.crm_credential == MENTOR_IDENTITY.credential


@pytest.mark.parametrize(
    "outcome", [CredentialsRejectedError("no"), CrmUnavailableError("down")]
)
def test_the_typed_crm_outcomes_pass_through_the_composed_verifier(
    outcome: Exception,
) -> None:
    verifier = CrmCredentialVerifier(FakeVerification(outcome), InMemoryIdentityBridge())

    with pytest.raises(type(outcome)):
        verifier.verify("mentor@cbm.org", "wrong")


def test_the_forgot_password_flow_forwards_to_the_crm_seam() -> None:
    forgot = RecordingForgotPassword()

    CrmForgotPasswordFlow(forgot).initiate("mentor@cbm.org", "mentor@cbm.org")

    assert forgot.requests == [("mentor@cbm.org", "mentor@cbm.org")]


class FakeEspoSite:
    """A mock Espo ``api/v1``: one account, token issuance, 404 recovery misses."""

    def __init__(self) -> None:
        self.password_reset_bodies: list[bytes] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/App/user"):
            pair = b64decode(request.headers["Espo-Authorization"]).decode()
            if pair != "mentor@cbm.org:correct horse":
                return httpx.Response(401, json={"message": "Unauthorized"})
            return httpx.Response(
                200,
                json={
                    "token": "espo-issued-token",
                    "user": {
                        "id": "espo-user-1",
                        "userName": "mentor@cbm.org",
                        "name": "Mentor One",
                        "emailAddress": "mentor@cbm.org",
                        "teamsNames": {"team-1": "Mentors"},
                    },
                },
            )
        if request.url.path.endswith("/User/passwordChangeRequest"):
            self.password_reset_bodies.append(request.read())
            return httpx.Response(404, json={"message": "Not Found"})
        raise AssertionError(f"unexpected Espo path {request.url.path}")


@pytest.fixture()
def wired_client() -> TestClient:
    """The deployment chain with only the HTTP wire mocked out."""
    gateway = EspoAuthGateway(
        HttpxEspoTransport(
            "https://crm.example.org", transport=httpx.MockTransport(FakeEspoSite())
        )
    )
    sessions = SessionManagement(InMemorySessionStore())
    app = create_app()
    app.dependency_overrides[get_session_management] = lambda: sessions
    app.dependency_overrides[get_credential_verifier] = lambda: CrmCredentialVerifier(
        gateway, InMemoryIdentityBridge()
    )
    app.dependency_overrides[get_forgot_password_flow] = lambda: CrmForgotPasswordFlow(gateway)
    return TestClient(app)


def test_login_end_to_end_over_the_wired_espo_chain(wired_client: TestClient) -> None:
    answer = wired_client.post(
        "/auth/login",
        json={"loginName": "mentor@cbm.org", "password": "correct horse"},
    )

    assert answer.status_code == 200
    data = answer.json()["data"]
    assert data["sessionReference"]
    assert data["roleNames"] == ["Mentors"]


def test_espo_refusal_surfaces_as_the_generic_invalid_credentials(
    wired_client: TestClient,
) -> None:
    answer = wired_client.post(
        "/auth/login",
        json={"loginName": "mentor@cbm.org", "password": "wrong"},
    )

    assert answer.status_code == 401
    assert answer.json()["errors"][0]["code"] == "invalidCredentials"


def test_forgot_password_answers_constantly_over_espos_account_miss(
    wired_client: TestClient,
) -> None:
    answer = wired_client.post(
        "/auth/forgot-password",
        json={"loginName": "nobody@cbm.org", "emailAddress": "nobody@cbm.org"},
    )

    assert answer.status_code == 200
    assert answer.json()["data"] == {"resetRequestAccepted": True}
