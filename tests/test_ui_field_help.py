"""Subtle field help affordance: marker + hover/focus reveal, nothing when absent (WTK-077)."""

from __future__ import annotations

from mentorapp.ui.field_help import (
    HELP_MARKER_RENDERING,
    HELP_TEXT_KEY,
    FieldHelp,
    field_help,
    form_help_affordances,
)

SCHEMA_FIELDS = (
    {
        "fieldName": "menteeEmail",
        "fieldLabel": "Email",
        "helpText": "Where session summaries are sent.",
    },
    {"fieldName": "menteeName", "fieldLabel": "Name", "helpText": None},
    {"fieldName": "menteePhone", "fieldLabel": "Phone"},
    {"fieldName": "menteeNickname", "fieldLabel": "Nickname", "helpText": "   "},
)


# --- The rendering contract (REQ-040) ----------------------------------------------


def test_help_renders_as_info_marker_on_the_label() -> None:
    assert HELP_MARKER_RENDERING.marker == "infoMarker"
    assert HELP_MARKER_RENDERING.placement == "fieldLabel"


def test_help_reveals_on_hover_and_focus_never_permanently() -> None:
    assert HELP_MARKER_RENDERING.reveal == ("hover", "focus")
    # Never a permanent hint paragraph — the affordance stays subtle.
    assert HELP_MARKER_RENDERING.persistent is False


def test_the_marker_takes_no_tab_stop() -> None:
    # REQ-038: Tab stops only on editable fields; the marker is not a data input.
    assert HELP_MARKER_RENDERING.tab_stop is False


def test_the_wire_key_is_the_schema_payloads() -> None:
    assert HELP_TEXT_KEY == "helpText"


# --- Per-field decision ------------------------------------------------------------


def test_help_text_yields_the_affordance_verbatim() -> None:
    help_ = field_help(SCHEMA_FIELDS[0])
    assert help_ == FieldHelp(
        field_name="menteeEmail",
        field_label="Email",
        help_text="Where session summaries are sent.",
    )
    assert help_.rendering is HELP_MARKER_RENDERING


def test_null_help_text_renders_nothing() -> None:
    assert field_help(SCHEMA_FIELDS[1]) is None


def test_absent_help_text_key_renders_nothing() -> None:
    assert field_help(SCHEMA_FIELDS[2]) is None


def test_whitespace_only_help_text_renders_nothing() -> None:
    # A marker that reveals blankness is worse than none — cleared settings
    # remove the marker.
    assert field_help(SCHEMA_FIELDS[3]) is None


# --- The form's mapping ------------------------------------------------------------


def test_form_mapping_carries_only_fields_with_help() -> None:
    affordances = form_help_affordances(SCHEMA_FIELDS)
    assert set(affordances) == {"menteeEmail"}
    assert affordances["menteeEmail"].help_text == "Where session summaries are sent."


def test_form_mapping_is_empty_when_no_field_has_help() -> None:
    assert form_help_affordances(SCHEMA_FIELDS[1:]) == {}
