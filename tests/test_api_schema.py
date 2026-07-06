"""``GET /schema/{entity}`` — the metadata endpoint contract (REQ-050)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from mentorapp.api.deps import get_session
from mentorapp.main import create_app
from mentorapp.storage import OptionSet, OptionValue, SchemaRegistry


@pytest.fixture()
def client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


@pytest.fixture()
def seeded(session: Session) -> None:
    status_set = OptionSet(option_set_name="mentorStatusSet")
    active = OptionValue(
        option_set=status_set,
        option_value_name="active",
        option_value_label="Active",
        option_value_sort_order=1,
    )
    retired = OptionValue(
        option_set=status_set,
        option_value_name="onLeave",
        option_value_label="On Leave",
        option_value_sort_order=2,
        active_flag=False,
    )
    deleted = OptionValue(
        option_set=status_set,
        option_value_name="ghost",
        option_value_label="Ghost",
        option_value_sort_order=3,
        deleted_at=datetime.now(UTC),
    )
    name_field = SchemaRegistry(
        entity_type="mentor",
        field_name="mentorName",
        field_type="text",
        field_label="Name",
        required_flag=True,
        searchable_flag=True,
        help_text="The mentor's full legal name.",
    )
    status_field = SchemaRegistry(
        entity_type="mentor",
        field_name="mentorStatus",
        field_type="choice",
        field_label="Status",
        option_set=status_set,
        history_tracked_flag=True,
    )
    removed_field = SchemaRegistry(
        entity_type="mentor",
        field_name="mentorLegacyCode",
        field_type="text",
        field_label="Legacy Code",
        deleted_at=datetime.now(UTC),
    )
    other_entity_field = SchemaRegistry(
        entity_type="engagement",
        field_name="engagementStatus",
        field_type="choice",
        field_label="Status",
        option_set=status_set,
    )
    session.add_all(
        [active, retired, deleted, name_field, status_field, removed_field, other_entity_field]
    )
    session.commit()


def test_schema_endpoint_serves_live_fields_in_the_envelope(
    client: TestClient, seeded: None
) -> None:
    resp = client.get("/schema/mentor")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"data", "meta", "errors"}
    assert body["errors"] is None
    assert body["data"]["entityType"] == "mentor"
    assert body["meta"]["fieldCount"] == 2
    # Deterministic order by fieldName; the soft-deleted row is not served.
    assert [f["fieldName"] for f in body["data"]["fields"]] == ["mentorName", "mentorStatus"]
    name_field = body["data"]["fields"][0]
    assert name_field["requiredFlag"] is True
    assert name_field["searchableFlag"] is True
    assert name_field["optionSet"] is None
    # REQ-040: the field setting's help text rides the one metadata endpoint.
    assert name_field["helpText"] == "The mentor's full legal name."
    assert body["data"]["fields"][1]["helpText"] is None


def test_choice_field_carries_its_option_set_inline(client: TestClient, seeded: None) -> None:
    body = client.get("/schema/mentor").json()
    status_field = body["data"]["fields"][1]
    assert status_field["historyTrackedFlag"] is True
    option_set = status_field["optionSet"]
    assert option_set["optionSetName"] == "mentorStatusSet"
    values = option_set["optionValues"]
    # Sort order preserved; retired value still served (historical records must
    # render it) with activeFlag off; soft-deleted value never served.
    assert [(v["optionValueName"], v["activeFlag"]) for v in values] == [
        ("active", True),
        ("onLeave", False),
    ]


def test_unknown_entity_is_404_in_the_envelope(client: TestClient, seeded: None) -> None:
    resp = client.get("/schema/unicorn")
    assert resp.status_code == 404
    body = resp.json()
    assert body["data"] is None
    assert body["errors"][0]["code"] == "notFound"
