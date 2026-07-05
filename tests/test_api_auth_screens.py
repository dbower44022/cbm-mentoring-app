"""``GET /auth/screens`` — the served credential-screen view-models (WTK-199).

The endpoint's whole contract is fidelity: the shell renders these payloads
verbatim, so they must mirror the ``ui.auth_flows`` declarations exactly —
including the invariant the messages exist to carry: the CRM-outage wording is
DISTINCT from the rejection wording (an outage must never read as a wrong
password).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mentorapp.main import create_app
from mentorapp.ui.auth_flows import (
    FORGOT_PASSWORD_SCREEN,
    LOGIN_SCREEN,
    REAUTH_SCREEN,
    SIGN_IN_CRM_UNAVAILABLE,
    SIGN_IN_REJECTED,
)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _data(client: TestClient) -> dict:
    response = client.get("/auth/screens")
    assert response.status_code == 200
    body = response.json()
    assert body["errors"] is None
    return body["data"]


def test_serves_all_three_screens_unauthenticated(client: TestClient) -> None:
    # No session, no X-User-ID: the login screen must render before either exists.
    data = _data(client)
    assert set(data["screens"]) == {
        LOGIN_SCREEN.key,
        FORGOT_PASSWORD_SCREEN.key,
        REAUTH_SCREEN.key,
    }


def test_login_screen_mirrors_the_declaration(client: TestClient) -> None:
    login = _data(client)["screens"]["login"]
    assert login["title"] == LOGIN_SCREEN.title
    assert login["submitLabel"] == LOGIN_SCREEN.submit_label
    assert login["links"] == list(LOGIN_SCREEN.links)
    assert login["enterSubmits"] is True
    assert [f["name"] for f in login["fields"]] == ["username", "password"]
    assert login["fields"][1]["control"] == "password"
    assert all(f["readOnly"] is False for f in login["fields"])


def test_field_names_travel_camel_case(client: TestClient) -> None:
    # email_address → emailAddress: the same name the forgot-password body takes.
    forgot = _data(client)["screens"]["forgotPassword"]
    assert [f["name"] for f in forgot["fields"]] == ["username", "emailAddress"]
    assert forgot["fields"][1]["control"] == "email"


def test_reauth_screen_pins_the_username(client: TestClient) -> None:
    reauth = _data(client)["screens"]["reauth"]
    fields = {f["name"]: f for f in reauth["fields"]}
    assert fields["username"]["readOnly"] is True
    assert fields["password"]["readOnly"] is False


def test_messages_carry_the_educate_triple(client: TestClient) -> None:
    messages = _data(client)["messages"]
    assert set(messages) == {
        "signInRejected",
        "signInCrmUnavailable",
        "resetRequested",
        "reauthPrompt",
        "reauthWrongUser",
        "sessionEnded",
    }
    assert messages["signInRejected"] == SIGN_IN_REJECTED.as_payload()
    for message in messages.values():
        assert set(message) == {"whatHappened", "why", "whatNext"}


def test_outage_wording_is_distinct_from_rejection(client: TestClient) -> None:
    # The WTK-003 invariant, asserted on the served payloads themselves.
    messages = _data(client)["messages"]
    assert messages["signInCrmUnavailable"] == SIGN_IN_CRM_UNAVAILABLE.as_payload()
    assert messages["signInCrmUnavailable"] != messages["signInRejected"]
    assert "password" not in messages["signInCrmUnavailable"]["whatHappened"].lower()
