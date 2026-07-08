"""``/form-input`` — the REQ-034 HTTP skin (REL-004 block 1).

The engine behaviors live in ``test_api_form_input``; these tests pin the
wire contract: envelope shapes, the never-block/never-gate answers, and the
authenticated-surface refusal.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from identity_stub import header_user_id
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.main import create_app
from mentorapp.storage import PostalCode


@pytest.fixture()
def client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    # The D9 identity seam resolves sessions in production; these are not
    # session-lifecycle tests, so the stub names the acting user directly.
    app.dependency_overrides[get_current_user_id] = header_user_id
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {"X-User-ID": str(uuid.uuid4())}


def test_format_dispatches_on_field_type(client: TestClient) -> None:
    response = client.post(
        "/form-input/format",
        headers=_headers(),
        json={"fieldType": "phone", "value": "2165551234"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["value"] == "(216) 555-1234"


def test_format_never_gates_unrecognized_input(client: TestClient) -> None:
    response = client.post(
        "/form-input/format",
        headers=_headers(),
        json={"fieldType": "phone", "value": "  +44 20 7946 0958 "},
    )
    # Not a US shape: returned as typed (trimmed), validity stays REQ-033's job.
    assert response.json()["data"]["value"] == "+44 20 7946 0958"


def test_resolve_paste_fills_confident_components_and_keeps_the_remainder(
    client: TestClient,
) -> None:
    response = client.post(
        "/form-input/resolve-paste",
        headers=_headers(),
        json={"fieldType": "personName", "text": "Lovelace, Ada"},
    )
    data = response.json()["data"]
    assert data["components"] == {"firstName": "Ada", "lastName": "Lovelace"}
    assert data["remainder"] == ""


def test_resolve_paste_never_blocks_an_unresolvable_field_type(
    client: TestClient,
) -> None:
    response = client.post(
        "/form-input/resolve-paste",
        headers=_headers(),
        json={"fieldType": "text", "text": "just some words"},
    )
    data = response.json()["data"]
    # The full text stays as remainder: the paste lands as typed.
    assert data == {"components": {}, "remainder": "just some words"}


def test_postal_autofill_answers_city_state_or_null(
    client: TestClient, session: Session
) -> None:
    session.add(PostalCode(postal_code_value="44113", city_name="Cleveland", state_code="OH"))
    session.commit()
    known = client.get(
        "/form-input/postal-autofill",
        headers=_headers(),
        params={"postal_code": "44113-2202"},
    )
    assert known.json()["data"]["fill"] == {"cityName": "Cleveland", "stateCode": "OH"}
    unknown = client.get(
        "/form-input/postal-autofill", headers=_headers(), params={"postal_code": "99999"}
    )
    # Unknown, not invalid: the reference table is a convenience, never a gate.
    assert unknown.status_code == 200
    assert unknown.json()["data"]["fill"] is None


def test_the_surface_is_authenticated(client: TestClient) -> None:
    response = client.post(
        "/form-input/format", json={"fieldType": "phone", "value": "2165551234"}
    )
    # No identity → the standard refusal, same as every other surface.
    assert response.status_code == 422
