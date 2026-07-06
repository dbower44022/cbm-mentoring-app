"""REQ-033 end-to-end (WTK-079): validation driven by settings as actually served.

``test_api_form_validation`` proves the engine's behaviors over hand-built
wire payloads; this suite closes the loop a mock cannot — every field here is
adapted from a real ``GET /schema/{entity}`` response over seeded registry
rows, so payload/adapter drift fails HERE, and editing a field's registry row
visibly changes its marker and validity with zero form code involved.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.api import (
    REQUIRED_MARKER,
    FieldSettings,
    form_label,
    sweep_before_save,
    validate_on_exit,
)
from mentorapp.api.deps import get_session
from mentorapp.main import create_app
from mentorapp.storage import OptionSet, OptionValue, SchemaRegistry

UNKNOWN_OPTION = "0197dead-0000-7000-8000-000000000000"


@pytest.fixture()
def client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


@pytest.fixture()
def seeded(session: Session) -> None:
    status_set = OptionSet(option_set_name="mentorStatusSet")
    session.add_all(
        [
            OptionValue(
                option_set=status_set,
                option_value_name="active",
                option_value_label="Active",
                option_value_sort_order=1,
            ),
            OptionValue(
                option_set=status_set,
                option_value_name="retired",
                option_value_label="Retired",
                option_value_sort_order=2,
                active_flag=False,
            ),
            SchemaRegistry(
                entity_type="mentor",
                field_name="mentorCapacity",
                field_type="number",
                field_label="Capacity",
            ),
            SchemaRegistry(
                entity_type="mentor",
                field_name="mentorName",
                field_type="text",
                field_label="Mentor Name",
                required_flag=True,
            ),
            SchemaRegistry(
                entity_type="mentor",
                field_name="mentorNotes",
                field_type="text",
                field_label="Notes",
            ),
            SchemaRegistry(
                entity_type="mentor",
                field_name="mentorStatus",
                field_type="choice",
                field_label="Status",
                required_flag=True,
                option_set=status_set,
            ),
        ]
    )
    session.commit()


def _served_form(client: TestClient) -> list[FieldSettings]:
    """The form exactly as served — every field adapted from the live payload."""
    body = client.get("/schema/mentor").json()
    return [FieldSettings.from_wire(field) for field in body["data"]["fields"]]


def test_adapter_accepts_every_field_as_actually_served(
    client: TestClient, seeded: None
) -> None:
    # from_wire must read the endpoint's REAL payload — including keys the
    # engine ignores (defaultValue, helpText) that a hand-built mock can omit.
    form = _served_form(client)
    assert [settings.field_name for settings in form] == [
        "mentorCapacity",
        "mentorName",
        "mentorNotes",
        "mentorStatus",
    ]
    status = form[3]
    assert status.option_set is not None
    # The retired value is served (historical records render it) and carries
    # activeFlag off — the exact signal the validator's inactiveOption reads.
    assert [value.active_flag for value in status.option_set.option_values] == [True, False]


def test_required_markers_come_from_served_settings(client: TestClient, seeded: None) -> None:
    labels = {settings.field_name: form_label(settings) for settings in _served_form(client)}
    assert labels == {
        "mentorCapacity": "Capacity",
        "mentorName": f"Mentor Name {REQUIRED_MARKER}",
        "mentorNotes": "Notes",
        "mentorStatus": f"Status {REQUIRED_MARKER}",
    }


def test_on_exit_validates_against_served_option_ids(client: TestClient, seeded: None) -> None:
    status = {settings.field_name: settings for settings in _served_form(client)}["mentorStatus"]
    assert status.option_set is not None
    served = {
        value.option_value_label: value.option_value_id
        for value in status.option_set.option_values
    }
    assert validate_on_exit(status, served["Active"]) is None
    retired = validate_on_exit(status, served["Retired"])
    unknown = validate_on_exit(status, UNKNOWN_OPTION)
    assert retired is not None and retired["code"] == "inactiveOption"
    assert unknown is not None and unknown["code"] == "unknownOption"


def test_sweep_over_served_form_reports_all_and_focuses_first(
    client: TestClient, seeded: None
) -> None:
    # Bad capacity, name never touched, status never touched: ALL three fail
    # in one sweep, inline, and focus goes to the first in display order.
    sweep = sweep_before_save(_served_form(client), {"mentorCapacity": "many", "mentorNotes": ""})
    assert not sweep.ok
    assert [(error["fieldName"], error["code"]) for error in sweep.inline] == [
        ("mentorCapacity", "typeMismatch"),
        ("mentorName", "requiredField"),
        ("mentorStatus", "requiredField"),
    ]
    assert sweep.focus_field_name == "mentorCapacity"
    assert sweep.form_level == ()


def test_editing_the_registry_row_flips_marker_and_validity(
    client: TestClient, seeded: None, session: Session
) -> None:
    # The REQ-033 founding claim: change the field setting, every form that
    # shows the field changes — no form code touched.
    before = {settings.field_name: settings for settings in _served_form(client)}
    assert form_label(before["mentorNotes"]) == "Notes"
    assert validate_on_exit(before["mentorNotes"], None) is None

    notes_row = session.scalars(
        select(SchemaRegistry).where(SchemaRegistry.field_name == "mentorNotes")
    ).one()
    notes_row.required_flag = True
    session.commit()

    after = {settings.field_name: settings for settings in _served_form(client)}
    assert form_label(after["mentorNotes"]) == f"Notes {REQUIRED_MARKER}"
    error = validate_on_exit(after["mentorNotes"], None)
    assert error is not None and error["code"] == "requiredField"
