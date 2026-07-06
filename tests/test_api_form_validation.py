"""Field-settings-driven validation engine design (REQ-033, WTK-057).

Field settings are wire-shaped ``GET /schema/{entity}`` payloads and the
engine is pure over them, so no database is needed — the API-side run of the
SAME validator is covered by ``test_api_processes``.
"""

from __future__ import annotations

from typing import Any

from mentorapp.api import (
    MESSAGE_PLACEMENT,
    REQUIRED_MARKER,
    FieldSettings,
    field_error,
    form_label,
    normalized_input,
    place_save_errors,
    request_error,
    sweep_before_save,
    validate_on_exit,
)

ACTIVE_OPTION = "0197b000-0000-7000-8000-000000000001"
RETIRED_OPTION = "0197b000-0000-7000-8000-000000000002"


def _wire_field(name: str, **overrides: Any) -> dict[str, Any]:
    """One field payload exactly as ``routers.schema._field_payload`` serves it."""
    payload: dict[str, Any] = {
        "fieldName": name,
        "fieldType": "text",
        "fieldLabel": name.capitalize(),
        "requiredFlag": False,
        "validationRules": None,
        "historyTrackedFlag": False,
        "searchableFlag": False,
        "visibilityHints": None,
        "userDefinedFlag": False,
        "optionSet": None,
    }
    payload.update(overrides)
    return payload


MENTOR_NAME = FieldSettings.from_wire(
    _wire_field("mentorName", fieldLabel="Mentor Name", requiredFlag=True)
)
MENTOR_NOTES = FieldSettings.from_wire(_wire_field("mentorNotes", fieldLabel="Notes"))
MENTOR_CAPACITY = FieldSettings.from_wire(
    _wire_field("mentorCapacity", fieldType="number", fieldLabel="Capacity")
)
MENTOR_STATUS = FieldSettings.from_wire(
    _wire_field(
        "mentorStatus",
        fieldType="choice",
        fieldLabel="Status",
        requiredFlag=True,
        optionSet={
            "optionSetID": "0197a000-0000-7000-8000-000000000009",
            "optionSetName": "mentorStatuses",
            "optionValues": [
                {
                    "optionValueID": ACTIVE_OPTION,
                    "optionValueName": "active",
                    "optionValueLabel": "Active",
                    "optionValueSortOrder": 1,
                    "activeFlag": True,
                },
                {
                    "optionValueID": RETIRED_OPTION,
                    "optionValueName": "legacy",
                    "optionValueLabel": "Legacy",
                    "optionValueSortOrder": 2,
                    "activeFlag": False,
                },
            ],
        },
    )
)

# The form in display order — the order that defines "first problem".
FORM = [MENTOR_NAME, MENTOR_CAPACITY, MENTOR_STATUS, MENTOR_NOTES]


# --- Required marker (settings-sourced, never per-form) ------------------------------


def test_required_field_label_carries_the_marker() -> None:
    assert form_label(MENTOR_NAME) == f"Mentor Name {REQUIRED_MARKER}"


def test_optional_field_label_has_no_marker() -> None:
    assert form_label(MENTOR_NOTES) == "Notes"


def test_messages_place_inline_at_the_field() -> None:
    assert MESSAGE_PLACEMENT == "inlineAtField"


# --- Per-field on-exit validation -----------------------------------------------------


def test_on_exit_flags_only_the_exited_field() -> None:
    error = validate_on_exit(MENTOR_NAME, None)
    assert error == field_error("mentorName", "requiredField", "This field is required.")


def test_on_exit_blank_text_means_no_value() -> None:
    # A cleared input yields "" — the engine normalizes it so a required
    # field reads as missing on exit AND in the payload the server sees.
    error = validate_on_exit(MENTOR_NAME, "   ")
    assert error is not None
    assert error["code"] == "requiredField"
    assert normalized_input("   ") is None
    assert normalized_input("Dana") == "Dana"


def test_on_exit_accepts_a_valid_value() -> None:
    assert validate_on_exit(MENTOR_NAME, "Dana Whitfield") is None


def test_on_exit_optional_empty_is_fine() -> None:
    assert validate_on_exit(MENTOR_NOTES, "") is None


def test_on_exit_type_mismatch_speaks_the_server_code() -> None:
    error = validate_on_exit(MENTOR_CAPACITY, "three")
    assert error is not None
    assert (error["fieldName"], error["code"]) == ("mentorCapacity", "typeMismatch")


def test_on_exit_choice_validates_against_the_option_set() -> None:
    assert validate_on_exit(MENTOR_STATUS, ACTIVE_OPTION) is None
    unknown = validate_on_exit(MENTOR_STATUS, "0197dead-0000-7000-8000-000000000000")
    retired = validate_on_exit(MENTOR_STATUS, RETIRED_OPTION)
    assert unknown is not None and unknown["code"] == "unknownOption"
    assert retired is not None and retired["code"] == "inactiveOption"


# --- Save sweep: all problems, focus the first in display order -----------------------


def test_sweep_reports_all_problems_and_focuses_the_first() -> None:
    sweep = sweep_before_save(
        FORM, {"mentorName": "", "mentorCapacity": "lots", "mentorStatus": ACTIVE_OPTION}
    )
    assert not sweep.ok
    assert [error["fieldName"] for error in sweep.inline] == [
        "mentorName",
        "mentorCapacity",
    ]
    assert sweep.focus_field_name == "mentorName"
    assert sweep.form_level == ()


def test_sweep_checks_untouched_required_fields() -> None:
    # mentorStatus was never touched: the sweep still fails it (required).
    sweep = sweep_before_save(FORM, {"mentorName": "Dana", "mentorCapacity": 3})
    assert [error["fieldName"] for error in sweep.inline] == ["mentorStatus"]
    assert sweep.focus_field_name == "mentorStatus"


def test_clean_sweep_lets_the_save_proceed() -> None:
    sweep = sweep_before_save(
        FORM,
        {
            "mentorName": "Dana",
            "mentorCapacity": 3,
            "mentorStatus": ACTIVE_OPTION,
            "mentorNotes": None,
        },
    )
    assert sweep.ok
    assert sweep.focus_field_name is None


# --- Server errors placed back onto the form -------------------------------------------


def test_server_errors_inline_in_display_order_and_focus_first_seen() -> None:
    # The server reported capacity first; the form focuses what the user
    # sees first — mentorName is earlier in display order.
    errors = [
        field_error("mentorCapacity", "typeMismatch", "Expected a number."),
        field_error("mentorName", "requiredField", "This field is required."),
    ]
    placed = place_save_errors(FORM, errors)
    assert [error["fieldName"] for error in placed.inline] == [
        "mentorName",
        "mentorCapacity",
    ]
    assert placed.focus_field_name == "mentorName"


def test_errors_no_displayed_field_owns_surface_at_form_level() -> None:
    errors = [
        request_error("duplicateCandidates", "A matching record already exists."),
        field_error("mentorRegion", "requiredField", "This field is required."),
        field_error("mentorName", "requiredField", "This field is required."),
    ]
    placed = place_save_errors(FORM, errors)
    assert not placed.ok
    assert [error["fieldName"] for error in placed.inline] == ["mentorName"]
    # Nothing is dropped: the request-level entry and the undisplayed field
    # both land at form level.
    assert [error["code"] for error in placed.form_level] == [
        "duplicateCandidates",
        "requiredField",
    ]
    assert placed.focus_field_name == "mentorName"
