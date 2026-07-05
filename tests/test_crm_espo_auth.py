"""EspoCRM authentication integration design (WTK-003): seams and the Espo plug.

Covers the four designed behaviours — login verification producing
``VerifiedIdentity``, the connected forgot-password flow, user-as-user
``CrmAccess`` execution, and the failure-outcome taxonomy — over a fake
:class:`EspoTransport`, plus the credential-never-in-repr guarantee.
"""

from __future__ import annotations

from base64 import b64encode
from collections.abc import Mapping
from typing import Any

import pytest

from mentorapp.crm import (
    CredentialsRejectedError,
    CrmCredentialExpiredError,
    CrmUnavailableError,
    CrmUserCredential,
    EspoAuthGateway,
    EspoOperationRejectedError,
    EspoResponse,
)


class FakeTransport:
    """Queue of canned Espo answers; records every request it was sent."""

    def __init__(self, *responses: EspoResponse | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def send(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> EspoResponse:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "headers": dict(headers),
                "params": params,
                "json": json,
            }
        )
        answer = self._responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


LOGIN_OK = EspoResponse(
    200,
    {
        "token": "espo-token-123",
        "user": {
            "id": "6839f0",
            "userName": "mentor.jane",
            "name": "Jane Mentor",
            "emailAddress": "jane@example.org",
        },
    },
)


def _basic(username: str, secret: str) -> str:
    return b64encode(f"{username}:{secret}".encode()).decode("ascii")


def test_verify_produces_identity_holding_the_issued_token_not_the_password() -> None:
    transport = FakeTransport(LOGIN_OK)
    identity = EspoAuthGateway(transport).verify("mentor.jane", "pa55word")

    assert identity.crm_user_id == "6839f0"
    assert identity.username == "mentor.jane"
    assert identity.display_name == "Jane Mentor"
    assert identity.email_address == "jane@example.org"
    assert identity.credential.secret == "espo-token-123"
    assert "pa55word" not in identity.credential.secret

    call = transport.calls[0]
    assert (call["method"], call["path"]) == ("GET", "App/user")
    assert call["headers"]["Espo-Authorization"] == _basic("mentor.jane", "pa55word")
    assert call["headers"]["Espo-Authorization-Create-Token"] == "true"


def test_verify_maps_espo_401_to_credentials_rejected() -> None:
    gateway = EspoAuthGateway(FakeTransport(EspoResponse(401, None)))
    with pytest.raises(CredentialsRejectedError):
        gateway.verify("mentor.jane", "wrong")


def test_verify_maps_espo_outage_to_unavailable_never_rejected() -> None:
    gateway = EspoAuthGateway(FakeTransport(EspoResponse(503, None)))
    with pytest.raises(CrmUnavailableError):
        gateway.verify("mentor.jane", "pa55word")


def test_verify_maps_transport_failure_to_unavailable_with_cause() -> None:
    boom = ConnectionError("no route to CRM")
    gateway = EspoAuthGateway(FakeTransport(boom))
    with pytest.raises(CrmUnavailableError) as excinfo:
        gateway.verify("mentor.jane", "pa55word")
    assert excinfo.value.__cause__ is boom


def test_verify_success_without_a_token_is_unavailable_not_a_login() -> None:
    tokenless = EspoResponse(200, {"user": {"id": "6839f0", "userName": "mentor.jane"}})
    gateway = EspoAuthGateway(FakeTransport(tokenless))
    with pytest.raises(CrmUnavailableError):
        gateway.verify("mentor.jane", "pa55word")


def test_password_reset_forwards_to_the_connected_espo_flow() -> None:
    transport = FakeTransport(EspoResponse(200, True))
    EspoAuthGateway(transport).request_password_reset("mentor.jane", "jane@example.org")

    call = transport.calls[0]
    assert (call["method"], call["path"]) == ("POST", "User/passwordChangeRequest")
    assert call["json"] == {"userName": "mentor.jane", "emailAddress": "jane@example.org"}


def test_password_reset_outcome_is_uniform_when_no_account_matches() -> None:
    gateway = EspoAuthGateway(FakeTransport(EspoResponse(404, None)))
    assert gateway.request_password_reset("nobody", "nobody@example.org") is None


def test_password_reset_surfaces_espo_outage() -> None:
    gateway = EspoAuthGateway(FakeTransport(EspoResponse(500, None)))
    with pytest.raises(CrmUnavailableError):
        gateway.request_password_reset("mentor.jane", "jane@example.org")


def test_execute_runs_as_the_users_own_account_via_the_token() -> None:
    transport = FakeTransport(EspoResponse(200, {"id": "acc-1", "name": "Acme"}))
    credential = CrmUserCredential(username="mentor.jane", secret="espo-token-123")
    payload = EspoAuthGateway(transport).execute(credential, "GET", "Account/acc-1")

    assert payload == {"id": "acc-1", "name": "Acme"}
    call = transport.calls[0]
    assert call["headers"]["Espo-Authorization"] == _basic("mentor.jane", "espo-token-123")
    assert call["headers"]["Espo-Authorization-By-Token"] == "true"


def test_execute_maps_dropped_token_to_credential_expired() -> None:
    credential = CrmUserCredential(username="mentor.jane", secret="stale-token")
    gateway = EspoAuthGateway(FakeTransport(EspoResponse(401, None)))
    with pytest.raises(CrmCredentialExpiredError):
        gateway.execute(credential, "GET", "Account/acc-1")


def test_execute_surfaces_operation_refusal_with_espo_answer() -> None:
    credential = CrmUserCredential(username="mentor.jane", secret="espo-token-123")
    refusal = EspoResponse(403, {"message": "forbidden"})
    gateway = EspoAuthGateway(FakeTransport(refusal))
    with pytest.raises(EspoOperationRejectedError) as excinfo:
        gateway.execute(credential, "PUT", "Account/acc-1", json={"name": "New"})
    assert excinfo.value.status_code == 403
    assert excinfo.value.payload == {"message": "forbidden"}


def test_execute_maps_server_failure_to_unavailable() -> None:
    credential = CrmUserCredential(username="mentor.jane", secret="espo-token-123")
    gateway = EspoAuthGateway(FakeTransport(EspoResponse(502, None)))
    with pytest.raises(CrmUnavailableError):
        gateway.execute(credential, "GET", "Account/acc-1")


def test_credential_secret_never_appears_in_reprs() -> None:
    transport = FakeTransport(LOGIN_OK)
    identity = EspoAuthGateway(transport).verify("mentor.jane", "pa55word")
    assert "espo-token-123" not in repr(identity)
    assert "espo-token-123" not in repr(identity.credential)
