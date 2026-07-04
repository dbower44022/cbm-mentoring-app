"""Metadata-driven-UI parity (WTK-149): the UI-facing contracts serve
built-in and user-defined fields identically.

REQ-049: ``GET /schema/{entity}`` speaks one payload shape for both field
kinds (``userDefinedFlag`` is the only tell), records serve both flat in one
vocabulary through write and read, and validation failures carry the same
codes for both.
REQ-014: a long operation (export) runs as a background job whose artifact
serves promoted user-defined columns beside built-in ones.
REQ-060 (preference round-trip + org-default fallback) is already covered by
test_api_preferences.py; nothing here re-tests it.

PostalCode is the guinea-pig entity for record-level tests, as in
test_api_process_guarantees — the engines are generic.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from mentorapp.api import ApiValidationError, create_record, partial_update, serialize_record
from mentorapp.api.deps import get_session
from mentorapp.automation import (
    EXPORT_JOB_TYPE,
    enqueue_job,
    export_job_handler,
    process_next_job,
)
from mentorapp.main import create_app
from mentorapp.storage import (
    OptionSet,
    OptionValue,
    PostalCode,
    SchemaRegistry,
    regenerate_read_views,
)

ENTITY = "postalCode"


def _register(session: Session, field_name: str, **overrides: Any) -> None:
    kwargs: dict[str, Any] = {"field_type": "text", "field_label": field_name, **overrides}
    session.add(SchemaRegistry(entity_type=ENTITY, field_name=field_name, **kwargs))


@pytest.fixture()
def registry(session: Session) -> None:
    """postalCode registry: three built-in fields plus one user-defined,
    both text-typed so any behavioral difference is field-kind, not type."""
    _register(session, "postalCodeValue", required_flag=True)
    _register(session, "cityName", required_flag=True)
    _register(session, "stateCode", required_flag=True)
    _register(session, "regionLabel", user_defined_flag=True)
    session.flush()


@pytest.fixture()
def client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def _create_springfield(session: Session, **extra: Any) -> PostalCode:
    return create_record(
        session,
        PostalCode,
        ENTITY,
        {"postalCodeValue": "62701", "cityName": "Springfield", "stateCode": "IL", **extra},
    )


# --- REQ-049: one schema payload shape for both field kinds --------------------


def test_schema_serves_one_payload_shape_for_both_field_kinds(
    client: TestClient, session: Session
) -> None:
    # A registry-only entity: /schema reads the registry alone, so choice
    # fields need no backing table here. One built-in and one user-defined
    # field per type (text, choice) — parity must hold with and without an
    # option set.
    status_set = OptionSet(option_set_name="profileStatusSet")
    tier_set = OptionSet(option_set_name="profileTierSet")
    session.add_all(
        [
            OptionValue(
                option_set=status_set,
                option_value_name="active",
                option_value_label="Active",
                option_value_sort_order=1,
            ),
            OptionValue(
                option_set=tier_set,
                option_value_name="gold",
                option_value_label="Gold",
                option_value_sort_order=1,
            ),
            SchemaRegistry(
                entity_type="mentorProfile",
                field_name="profileName",
                field_type="text",
                field_label="Name",
            ),
            SchemaRegistry(
                entity_type="mentorProfile",
                field_name="profileMotto",
                field_type="text",
                field_label="Motto",
                user_defined_flag=True,
            ),
            SchemaRegistry(
                entity_type="mentorProfile",
                field_name="profileStatus",
                field_type="choice",
                field_label="Status",
                option_set=status_set,
            ),
            SchemaRegistry(
                entity_type="mentorProfile",
                field_name="profileTier",
                field_type="choice",
                field_label="Tier",
                option_set=tier_set,
                user_defined_flag=True,
            ),
        ]
    )
    session.commit()

    body = client.get("/schema/mentorProfile").json()
    fields = {f["fieldName"]: f for f in body["data"]["fields"]}
    assert set(fields) == {"profileName", "profileMotto", "profileStatus", "profileTier"}

    key_sets = {name: frozenset(payload) for name, payload in fields.items()}
    assert len(set(key_sets.values())) == 1, key_sets
    assert {name: f["userDefinedFlag"] for name, f in fields.items()} == {
        "profileName": False,
        "profileMotto": True,
        "profileStatus": False,
        "profileTier": True,
    }
    # Choice fields carry their option set inline with one shape, both kinds.
    builtin_set = fields["profileStatus"]["optionSet"]
    custom_set = fields["profileTier"]["optionSet"]
    assert frozenset(builtin_set) == frozenset(custom_set)
    assert frozenset(builtin_set["optionValues"][0]) == frozenset(custom_set["optionValues"][0])


# --- REQ-049: records serve both kinds flat through write and read -------------


def test_record_round_trip_serves_user_defined_values_flat_beside_built_in(
    session: Session, registry: None
) -> None:
    record = _create_springfield(session, regionLabel="Central")
    session.commit()

    payload = serialize_record(record)
    assert payload["cityName"] == "Springfield"
    assert payload["regionLabel"] == "Central"
    # The bag itself is never served — its members are promoted, so the UI
    # has no marker to tell the field kinds apart.
    assert "customAttributes" not in payload

    partial_update(
        session, record, ENTITY, {"regionLabel": "Downstate"}, row_version=record.row_version
    )
    session.commit()
    assert serialize_record(record)["regionLabel"] == "Downstate"


def test_validation_speaks_the_same_codes_for_both_field_kinds(
    session: Session, registry: None
) -> None:
    with pytest.raises(ApiValidationError) as excinfo:
        create_record(
            session,
            PostalCode,
            ENTITY,
            {"postalCodeValue": "62701", "cityName": 5, "stateCode": "IL", "regionLabel": 7},
        )
    codes = {error["fieldName"]: error["code"] for error in excinfo.value.errors}
    assert codes == {"cityName": "typeMismatch", "regionLabel": "typeMismatch"}


# --- REQ-014: the long operation runs as a background job, both kinds served ----


class _Store:
    """In-memory ArtifactStore: just enough surface for the export handler."""

    def __init__(self) -> None:
        self.artifacts: dict[str, tuple[bytes, str]] = {}

    def put(self, name: str, content: bytes, content_type: str) -> str:
        url = f"https://artifacts.test/{name}"
        self.artifacts[url] = (content, content_type)
        return url

    def discard(self, url: str) -> None:
        self.artifacts.pop(url, None)


def test_export_background_job_serves_user_defined_columns_beside_built_in(
    session: Session, registry: None
) -> None:
    _create_springfield(session, regionLabel="Central")
    session.commit()
    regenerate_read_views(session)

    store = _Store()
    job = enqueue_job(
        session,
        EXPORT_JOB_TYPE,
        {"entityType": ENTITY, "columns": ["cityName", "regionLabel"]},
    )
    session.commit()
    assert process_next_job(session, {EXPORT_JOB_TYPE: export_job_handler(store)}) is True

    assert job.job_status == "completed"
    content, content_type = store.artifacts[job.artifact_url]
    assert content_type == "text/csv"
    lines = content.decode("utf-8").splitlines()
    # The read view promotes the user-defined member to a named column: the
    # artifact addresses and renders both kinds through one column contract.
    assert lines[0] == "cityName,regionLabel"
    assert "Springfield,Central" in lines[1:]
