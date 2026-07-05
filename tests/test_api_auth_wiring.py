"""Wiring smoke (WTK-191): ``POST /auth/login`` through the production providers.

The app factory's real wiring runs end-to-end — Espo gateway (over a fake
transport), stored identity bridge, stored session store, sealed credential —
with only the two seams a deployment also swaps overridden: the DB session
and the HTTP transport. Keys arrive via the documented environment variables.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from mentorapp.api.deps import get_session
from mentorapp.api.wiring import get_espo_transport
from mentorapp.crm.espo import EspoResponse
from mentorapp.main import create_app
from mentorapp.storage import AppUser, AuthSession

LOGIN = "mentor.jane"
PASSWORD = "correct-horse"
CRM_TOKEN = "espo-issued-token-0451"


class FakeEspoTransport:
    """Canned Espo: one account, token-issuing login answer, 401 otherwise."""

    def send(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> EspoResponse:
        expected = base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode("ascii")
        if path == "App/user" and headers.get("Espo-Authorization") == expected:
            return EspoResponse(
                200,
                {
                    "user": {
                        "id": "crm-1",
                        "userName": LOGIN,
                        "name": "Jane Mentor",
                        "emailAddress": "jane@cbm.org",
                        "teamsNames": {"1": "mentor"},
                    },
                    "token": CRM_TOKEN,
                },
            )
        return EspoResponse(401, None)


@pytest.fixture()
def client(session: Session, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(
        "MENTORAPP_CREDENTIAL_KEY", base64.urlsafe_b64encode(b"c" * 32).decode("ascii")
    )
    monkeypatch.setenv(
        "MENTORAPP_TOKEN_SIGNING_KEY", base64.urlsafe_b64encode(b"s" * 32).decode("ascii")
    )
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_espo_transport] = FakeEspoTransport
    return TestClient(app)


def test_login_persists_a_real_session_with_a_sealed_credential(
    client: TestClient, session: Session
) -> None:
    response = client.post("/auth/login", json={"loginName": LOGIN, "password": PASSWORD})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["roleNames"] == ["mentor"]

    user = session.scalars(select(AppUser).where(AppUser.crm_user_id == "crm-1")).one()
    assert str(user.user_id) == data["userID"]
    row = session.scalars(select(AuthSession).where(AuthSession.user_id == user.user_id)).one()
    assert data["sessionReference"].startswith(row.auth_session_id.hex)

    sealed = session.execute(
        text('SELECT "crmCredentialEncrypted" FROM "authSession"')
    ).scalar_one()
    assert sealed is not None
    assert CRM_TOKEN not in sealed


def test_second_login_reuses_the_provisioned_user(client: TestClient, session: Session) -> None:
    body = {"loginName": LOGIN, "password": PASSWORD}
    assert client.post("/auth/login", json=body).status_code == 200
    assert client.post("/auth/login", json=body).status_code == 200
    users = session.scalars(select(AppUser)).all()
    assert len(users) == 1
    assert len(session.scalars(select(AuthSession)).all()) == 2


def test_wrong_password_maps_to_the_generic_refusal(client: TestClient) -> None:
    response = client.post("/auth/login", json={"loginName": LOGIN, "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "invalidCredentials"
