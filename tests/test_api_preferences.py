"""``GET/PUT /preferences/{key}`` — the preference persistence contract (REQ-060)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from identity_stub import header_user_id
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.main import create_app
from mentorapp.storage import UserPreference, utcnow, uuid7

KEY = "grid.mentorRoster.columns"


@pytest.fixture()
def client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    # The D9 identity seam resolves sessions in production; these are not
    # session-lifecycle tests, so the stub names the acting user directly.
    app.dependency_overrides[get_current_user_id] = header_user_id
    return TestClient(app)


@pytest.fixture()
def user_id() -> uuid.UUID:
    return uuid7()


def _headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-ID": str(user_id)}


def _rows(session: Session, key: str) -> list[UserPreference]:
    return list(
        session.scalars(select(UserPreference).where(UserPreference.preference_key == key))
    )


def test_get_unset_preference_is_404_in_envelope(
    client: TestClient, user_id: uuid.UUID
) -> None:
    response = client.get(f"/preferences/{KEY}", headers=_headers(user_id))
    assert response.status_code == 404
    body = response.json()
    assert body["data"] is None
    assert body["errors"][0]["code"] == "notFound"


def test_put_creates_the_callers_row_and_stamps_audit(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    response = client.put(
        f"/preferences/{KEY}",
        headers=_headers(user_id),
        json={"preferenceValue": {"columns": ["mentorName"]}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["errors"] is None
    assert body["data"] == {
        "preferenceKey": KEY,
        "preferenceValue": {"columns": ["mentorName"]},
        "preferenceScope": "user",
    }
    (row,) = _rows(session, KEY)
    assert row.user_id == user_id
    assert row.created_by == user_id
    assert row.modified_by == user_id
    assert row.row_version == 1


def test_get_serves_the_org_default_when_the_user_has_no_row(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    session.add(UserPreference(preference_key=KEY, preference_value={"columns": ["a"]}))
    session.commit()
    body = client.get(f"/preferences/{KEY}", headers=_headers(user_id)).json()
    assert body["data"]["preferenceScope"] == "orgDefault"
    assert body["data"]["preferenceValue"] == {"columns": ["a"]}


def test_the_users_own_row_overrides_the_org_default(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    session.add(UserPreference(preference_key=KEY, preference_value={"columns": ["a"]}))
    session.add(
        UserPreference(user_id=user_id, preference_key=KEY, preference_value={"columns": ["b"]})
    )
    session.commit()
    body = client.get(f"/preferences/{KEY}", headers=_headers(user_id)).json()
    assert body["data"]["preferenceScope"] == "user"
    assert body["data"]["preferenceValue"] == {"columns": ["b"]}


def test_put_replaces_the_whole_document_and_bumps_the_version(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    client.put(
        f"/preferences/{KEY}", headers=_headers(user_id), json={"preferenceValue": {"a": 1}}
    )
    response = client.put(
        f"/preferences/{KEY}", headers=_headers(user_id), json={"preferenceValue": {"b": 2}}
    )
    assert response.json()["data"]["preferenceValue"] == {"b": 2}
    (row,) = _rows(session, KEY)
    assert row.preference_value == {"b": 2}
    assert row.row_version == 2


def test_an_unchanged_put_is_a_noop(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    payload = {"preferenceValue": {"a": 1}}
    client.put(f"/preferences/{KEY}", headers=_headers(user_id), json=payload)
    response = client.put(f"/preferences/{KEY}", headers=_headers(user_id), json=payload)
    assert response.status_code == 200
    (row,) = _rows(session, KEY)
    assert row.row_version == 1


def test_put_never_touches_the_org_default_row(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    session.add(UserPreference(preference_key=KEY, preference_value={"columns": ["a"]}))
    session.commit()
    client.put(
        f"/preferences/{KEY}", headers=_headers(user_id), json={"preferenceValue": {"b": 2}}
    )
    rows = _rows(session, KEY)
    assert len(rows) == 2
    org_row = next(row for row in rows if row.user_id is None)
    assert org_row.preference_value == {"columns": ["a"]}


def test_a_soft_deleted_user_row_falls_back_to_the_org_default(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    session.add(UserPreference(preference_key=KEY, preference_value={"columns": ["a"]}))
    session.add(
        UserPreference(
            user_id=user_id,
            preference_key=KEY,
            preference_value={"columns": ["b"]},
            deleted_at=utcnow(),
        )
    )
    session.commit()
    body = client.get(f"/preferences/{KEY}", headers=_headers(user_id)).json()
    assert body["data"]["preferenceScope"] == "orgDefault"


def test_a_missing_user_header_is_a_structured_422(client: TestClient) -> None:
    response = client.get(f"/preferences/{KEY}")
    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    assert body["errors"][0]["fieldName"] is not None


def test_an_overlong_key_is_rejected_per_field(client: TestClient, user_id: uuid.UUID) -> None:
    key = "nav." + "x" * 200
    response = client.put(
        f"/preferences/{key}", headers=_headers(user_id), json={"preferenceValue": {}}
    )
    assert response.status_code == 422
    error = response.json()["errors"][0]
    assert error["fieldName"] == "preferenceKey"
    assert error["code"] == "valueTooLong"


def test_a_non_object_document_is_rejected(client: TestClient, user_id: uuid.UUID) -> None:
    response = client.put(
        f"/preferences/{KEY}", headers=_headers(user_id), json={"preferenceValue": "not-a-doc"}
    )
    assert response.status_code == 422
    assert response.json()["errors"] is not None
