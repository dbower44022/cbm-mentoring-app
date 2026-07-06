"""``/help`` endpoints (WTK-100/WTK-104/WTK-108, REQ-043): resolution walks
mapping → pattern → home and never dead-ends; configuration is admin-gated
behind ``help.admin``; every failure rides the one envelope."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from mentorapp.access import CAP_HELP_ADMIN
from mentorapp.api.deps import get_session
from mentorapp.api.routers.help import (
    CODE_DUPLICATE_HELP_MAPPING,
    CODE_INVALID_HELP_URL,
    CODE_PATTERN_WITHOUT_PLACEHOLDER,
    CODE_UNKNOWN_HELP_SOURCE_TYPE,
    RESOLUTION_HOME,
    RESOLUTION_MAPPING,
    RESOLUTION_PATTERN,
    RESOLUTION_UNCONFIGURED,
)
from mentorapp.main import create_app
from mentorapp.storage import AccessGrant, AppUser, HelpMapping, HelpSettings

HOME = "https://docs.example.org/help"
PATTERN = "https://docs.example.org/help/{sourceType}/{sourceIdentifier}"


@pytest.fixture()
def app_client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def _user(session: Session, username: str) -> uuid.UUID:
    user = AppUser(crm_user_id=f"crm-{username}", username=username)
    session.add(user)
    session.flush()
    return user.user_id


@pytest.fixture()
def mentor_id(session: Session) -> uuid.UUID:
    return _user(session, "mentor.one")


@pytest.fixture()
def admin_id(session: Session) -> uuid.UUID:
    admin = _user(session, "admin.one")
    session.add(AccessGrant(user_id=admin, access_grant_key=CAP_HELP_ADMIN))
    session.flush()
    return admin


@pytest.fixture()
def settings(session: Session) -> HelpSettings:
    # The migration-0013 seed, reproduced for the create_all test schema:
    # the singleton exists from first boot with everything unconfigured.
    row = HelpSettings()
    session.add(row)
    session.flush()
    return row


def _headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-ID": str(user_id)}


def _mapping(
    session: Session,
    *,
    source_type: str = "panel",
    source_identifier: str = "engagements",
    help_url: str = "https://docs.example.org/help/panels/engagements",
) -> HelpMapping:
    mapping = HelpMapping(
        source_type=source_type, source_identifier=source_identifier, help_url=help_url
    )
    session.add(mapping)
    session.flush()
    return mapping


def _resolve(
    app_client: TestClient,
    user_id: uuid.UUID,
    source_type: str = "panel",
    source_identifier: str = "engagements",
) -> Any:
    return app_client.get(
        "/help/resolve",
        params={"sourceType": source_type, "sourceIdentifier": source_identifier},
        headers=_headers(user_id),
    )


# --- Resolution (REQ-043's walk: mapping -> pattern -> home) -------------------------


def test_mapped_page_resolves_to_its_url_without_notice(
    app_client: TestClient,
    session: Session,
    mentor_id: uuid.UUID,
    settings: HelpSettings,
) -> None:
    mapping = _mapping(session)
    response = _resolve(app_client, mentor_id)
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == {"url": mapping.help_url, "mapped": True, "notice": None}
    assert body["meta"]["resolution"] == RESOLUTION_MAPPING
    assert body["errors"] is None


def test_resolution_needs_no_admin_capability(
    app_client: TestClient,
    session: Session,
    mentor_id: uuid.UUID,
    settings: HelpSettings,
) -> None:
    # Help is every signed-in user's read (REQ-043): a plain mentor with no
    # grants resolves — only CONFIGURING is gated.
    _mapping(session)
    assert _resolve(app_client, mentor_id).status_code == 200


def test_unmapped_page_derives_from_the_configured_pattern(
    app_client: TestClient,
    session: Session,
    mentor_id: uuid.UUID,
    settings: HelpSettings,
) -> None:
    settings.default_url_pattern = PATTERN
    settings.help_home_url = HOME
    session.flush()
    # A display-name identifier (spaces) must land URL-encoded, never raw.
    response = _resolve(app_client, mentor_id, "workprocess", "Bulk Reassign Mentor")
    body = response.json()
    # Pattern-derived is page-specific, so it presents as mapped with no
    # notice; the truthful row-vs-pattern fact rides meta.resolution.
    assert body["data"] == {
        "url": "https://docs.example.org/help/workprocess/Bulk%20Reassign%20Mentor",
        "mapped": True,
        "notice": None,
    }
    assert body["meta"]["resolution"] == RESOLUTION_PATTERN


def test_unmapped_page_without_pattern_falls_back_to_home_with_educate_notice(
    app_client: TestClient,
    session: Session,
    mentor_id: uuid.UUID,
    settings: HelpSettings,
) -> None:
    settings.help_home_url = HOME
    session.flush()
    body = _resolve(app_client, mentor_id, "dataSet", "engagements").json()
    assert body["data"]["url"] == HOME
    assert body["data"]["mapped"] is False
    # The REQ-043 educate voice: what happened, and who can improve it —
    # phrased for the surface kind actually asked about.
    assert "No page-specific help exists yet for this data set" in body["data"]["notice"]
    assert "administrator" in body["data"]["notice"]
    assert body["meta"]["resolution"] == RESOLUTION_HOME


def test_fully_unconfigured_help_explains_itself_instead_of_dead_linking(
    app_client: TestClient,
    session: Session,
    mentor_id: uuid.UUID,
    settings: HelpSettings,
) -> None:
    body = _resolve(app_client, mentor_id).json()
    # No URL to open — the notice IS the answer; never a blank tab, never a 500.
    assert body["data"]["url"] is None
    assert body["data"]["mapped"] is False
    assert "isn't set up yet" in body["data"]["notice"]
    assert body["meta"]["resolution"] == RESOLUTION_UNCONFIGURED


def test_unknown_source_type_is_the_callers_mistake(
    app_client: TestClient,
    session: Session,
    mentor_id: uuid.UUID,
    settings: HelpSettings,
) -> None:
    response = _resolve(app_client, mentor_id, "dashboard", "sales")
    assert response.status_code == 422
    error = response.json()["errors"][0]
    assert error["code"] == CODE_UNKNOWN_HELP_SOURCE_TYPE
    assert error["fieldName"] == "sourceType"


def test_unmapping_changes_the_very_next_resolve(
    app_client: TestClient,
    session: Session,
    mentor_id: uuid.UUID,
    admin_id: uuid.UUID,
    settings: HelpSettings,
) -> None:
    settings.help_home_url = HOME
    session.flush()
    mapping = _mapping(session)
    assert _resolve(app_client, mentor_id).json()["data"]["url"] == mapping.help_url

    deleted = app_client.delete(
        f"/help/mappings/{mapping.help_mapping_id}", headers=_headers(admin_id)
    )
    assert deleted.status_code == 200
    assert deleted.json()["data"]["deleted"] is True

    body = _resolve(app_client, mentor_id).json()
    assert body["meta"]["resolution"] == RESOLUTION_HOME
    assert body["data"]["url"] == HOME


# --- Admin gating (help.admin through the one capability boundary) -------------------


def test_configuration_refuses_non_admins_with_the_capability_envelope(
    app_client: TestClient,
    session: Session,
    mentor_id: uuid.UUID,
    settings: HelpSettings,
) -> None:
    creation = app_client.post(
        "/help/mappings",
        json={
            "sourceType": "panel",
            "sourceIdentifier": "engagements",
            "helpURL": "https://docs.example.org/x",
        },
        headers=_headers(mentor_id),
    )
    assert creation.status_code == 403
    # Educate voice: names the capability so an admin knows what to grant.
    assert CAP_HELP_ADMIN in creation.json()["errors"][0]["message"]
    # Every configuration verb sits behind the same gate.
    assert app_client.get("/help/mappings", headers=_headers(mentor_id)).status_code == 403
    assert app_client.get("/help/settings", headers=_headers(mentor_id)).status_code == 403
    assert (
        app_client.patch(
            "/help/settings", json={"rowVersion": 1}, headers=_headers(mentor_id)
        ).status_code
        == 403
    )


# --- Mapping CRUD --------------------------------------------------------------------


def test_admin_creates_lists_and_edits_mappings(
    app_client: TestClient,
    session: Session,
    admin_id: uuid.UUID,
    settings: HelpSettings,
) -> None:
    created = app_client.post(
        "/help/mappings",
        json={
            "sourceType": "panel",
            "sourceIdentifier": "engagements",
            "helpURL": "https://docs.example.org/help/panels/engagements",
        },
        headers=_headers(admin_id),
    )
    assert created.status_code == 200
    record = created.json()["data"]
    assert record["rowVersion"] == 1

    listed = app_client.get("/help/mappings", headers=_headers(admin_id)).json()["data"]
    assert [m["helpMappingID"] for m in listed] == [record["helpMappingID"]]

    patched = app_client.patch(
        f"/help/mappings/{record['helpMappingID']}",
        json={"rowVersion": 1, "helpURL": "https://docs.example.org/v2"},
        headers=_headers(admin_id),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["helpURL"] == "https://docs.example.org/v2"
    assert patched.json()["data"]["rowVersion"] == 2


def test_mapping_writes_validate_vocabulary_url_and_uniqueness(
    app_client: TestClient,
    session: Session,
    admin_id: uuid.UUID,
    settings: HelpSettings,
) -> None:
    # Every gate reports in ONE round trip (DB-S12).
    response = app_client.post(
        "/help/mappings",
        json={
            "sourceType": "dashboard",
            "sourceIdentifier": "sales",
            "helpURL": "not-a-url",
        },
        headers=_headers(admin_id),
    )
    assert response.status_code == 422
    codes = {e["code"] for e in response.json()["errors"]}
    assert codes == {CODE_UNKNOWN_HELP_SOURCE_TYPE, CODE_INVALID_HELP_URL}

    _mapping(session)
    duplicate = app_client.post(
        "/help/mappings",
        json={
            "sourceType": "panel",
            "sourceIdentifier": "engagements",
            "helpURL": "https://docs.example.org/other",
        },
        headers=_headers(admin_id),
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["errors"][0]["code"] == CODE_DUPLICATE_HELP_MAPPING


def test_mapping_patch_cannot_land_on_another_live_surface(
    app_client: TestClient,
    session: Session,
    admin_id: uuid.UUID,
    settings: HelpSettings,
) -> None:
    _mapping(session)
    other = _mapping(session, source_identifier="mentors")
    moved = app_client.patch(
        f"/help/mappings/{other.help_mapping_id}",
        json={"rowVersion": 1, "sourceIdentifier": "engagements"},
        headers=_headers(admin_id),
    )
    assert moved.status_code == 422
    assert moved.json()["errors"][0]["code"] == CODE_DUPLICATE_HELP_MAPPING


def test_stale_mapping_write_answers_409_with_the_current_record(
    app_client: TestClient,
    session: Session,
    admin_id: uuid.UUID,
    settings: HelpSettings,
) -> None:
    mapping = _mapping(session)
    response = app_client.patch(
        f"/help/mappings/{mapping.help_mapping_id}",
        json={"rowVersion": 99, "helpURL": "https://docs.example.org/v2"},
        headers=_headers(admin_id),
    )
    assert response.status_code == 409
    body = response.json()
    assert body["errors"][0]["code"] == "staleRowVersion"
    # The recovery body: the CURRENT record, so the client can refresh.
    assert body["data"]["helpURL"] == mapping.help_url
    assert body["data"]["rowVersion"] == 1


# --- Settings ------------------------------------------------------------------------


def test_admin_reads_and_retunes_the_settings_singleton(
    app_client: TestClient,
    session: Session,
    admin_id: uuid.UUID,
    settings: HelpSettings,
) -> None:
    read = app_client.get("/help/settings", headers=_headers(admin_id)).json()["data"]
    assert read == {
        "helpSettingsID": str(settings.help_settings_id),
        "helpHomeURL": "",
        "defaultURLPattern": "",
        "rowVersion": 1,
    }

    patched = app_client.patch(
        "/help/settings",
        json={"rowVersion": 1, "helpHomeURL": HOME, "defaultURLPattern": PATTERN},
        headers=_headers(admin_id),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["helpHomeURL"] == HOME
    assert patched.json()["data"]["defaultURLPattern"] == PATTERN
    assert patched.json()["data"]["rowVersion"] == 2

    # Clearing is a sanctioned value (empty string), not a refusal: the
    # resolve answer explains an unconfigured system.
    cleared = app_client.patch(
        "/help/settings",
        json={"rowVersion": 2, "defaultURLPattern": ""},
        headers=_headers(admin_id),
    )
    assert cleared.status_code == 200
    assert cleared.json()["data"]["defaultURLPattern"] == ""


def test_settings_pattern_must_be_absolute_and_carry_a_placeholder(
    app_client: TestClient,
    session: Session,
    admin_id: uuid.UUID,
    settings: HelpSettings,
) -> None:
    fixed_url = app_client.patch(
        "/help/settings",
        json={"rowVersion": 1, "defaultURLPattern": "https://docs.example.org/fixed"},
        headers=_headers(admin_id),
    )
    assert fixed_url.status_code == 422
    assert fixed_url.json()["errors"][0]["code"] == CODE_PATTERN_WITHOUT_PLACEHOLDER

    relative = app_client.patch(
        "/help/settings",
        json={"rowVersion": 1, "helpHomeURL": "docs/help"},
        headers=_headers(admin_id),
    )
    assert relative.status_code == 422
    assert relative.json()["errors"][0]["code"] == CODE_INVALID_HELP_URL


def test_stale_settings_write_answers_409_with_the_current_document(
    app_client: TestClient,
    session: Session,
    admin_id: uuid.UUID,
    settings: HelpSettings,
) -> None:
    response = app_client.patch(
        "/help/settings",
        json={"rowVersion": 99, "helpHomeURL": HOME},
        headers=_headers(admin_id),
    )
    assert response.status_code == 409
    assert response.json()["data"]["rowVersion"] == 1
