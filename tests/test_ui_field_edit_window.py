"""Per-field edit window build gate: frame, gesture bridge, control (WTK-070)."""

from __future__ import annotations

from typing import Any

from mentorapp.api.field_edit import (
    FIELD_EDIT_WINDOW,
    CommitSingleField,
    FieldEditors,
    FieldEditRefused,
    FieldEditSwitch,
)
from mentorapp.ui.edit_form import EDIT_FORM_SCREEN, SAVE_SHORTCUT
from mentorapp.ui.entry_editors import RICH_TEXT_CONTROL
from mentorapp.ui.field_edit_window import (
    FIELD_EDIT_FRAME,
    SWITCH_TO_WINDOW_LABEL,
    OpenFieldWindow,
    TypedInput,
    open_from_double_click,
    switch_offer_message,
    window_control,
)
from mentorapp.ui.lookup_control import LOOKUP_CONTROL
from mentorapp.ui.readonly_fields import (
    PermissionBlock,
    classify_read_only,
    field_edit_reason,
)

NAME_FIELD = {"fieldName": "mentorName", "fieldLabel": "Name", "fieldType": "string"}
NOTES_FIELD = {"fieldName": "mentorNoteBody", "fieldLabel": "Notes", "fieldType": "richText"}
PROGRAM_FIELD = {
    "fieldName": "mentorProgramID",
    "fieldLabel": "Program",
    "fieldType": "reference",
}
SESSIONS_FIELD = {
    "fieldName": "mentorSessionCount",
    "fieldLabel": "Sessions held",
    "fieldType": "integer",
    "visibilityHints": {"computed": True},
}
RATE_FIELD = {"fieldName": "mentorRate", "fieldLabel": "Rate", "fieldType": "integer"}
RATE_BLOCK = PermissionBlock("Edit mentor rates", "your program administrator")

RECORD: dict[str, Any] = {
    "mentorID": "m-1",
    "mentorName": "Ada",
    "mentorNoteBody": "<p>Prefers mornings.</p>",
    "mentorProgramID": "p-1",
    "mentorSessionCount": 12,
    "mentorRate": 45,
    "rowVersion": 7,
    "modifiedAt": "2026-07-05T00:00:07Z",
    "modifiedBy": "someone-else",
}


def _open(
    editors: FieldEditors, key: str = "w1", spec: dict[str, Any] = NAME_FIELD
) -> OpenFieldWindow:
    outcome = open_from_double_click(editors, key, "mentor", spec, RECORD)
    assert isinstance(outcome, OpenFieldWindow)
    return outcome


# --- The frame (REQ-035) -------------------------------------------------------------


def test_frame_carries_the_api_window_declaration_verbatim() -> None:
    # The small-window shape (own Save/Cancel, single-field commit) has one
    # canonical home; the UI frame layers onto it, never restates it.
    assert FIELD_EDIT_FRAME.window is FIELD_EDIT_WINDOW
    assert FIELD_EDIT_FRAME.window.kind == "smallWindow"
    assert FIELD_EDIT_FRAME.window.commits == "singleFieldPatch"


def test_escape_cancels_here_unlike_the_full_form() -> None:
    # Forms standard: Esc = cancel in the per-field window; elsewhere Esc
    # requests close with the guard. The two frames must disagree.
    assert FIELD_EDIT_FRAME.escape == "cancel"
    assert EDIT_FORM_SCREEN.escape == "requestLeave"


def test_save_shortcut_is_the_one_shared_ctrl_s() -> None:
    assert FIELD_EDIT_FRAME.save_shortcut == SAVE_SHORTCUT


def test_focus_starts_in_the_editor_control() -> None:
    assert FIELD_EDIT_FRAME.initial_focus == "editorControl"


# --- The gesture bridge --------------------------------------------------------------


def test_double_click_on_an_editable_field_opens_the_window() -> None:
    editors = FieldEditors()
    window = _open(editors)
    assert window.opened.field_name == "mentorName"
    assert window.opened.base_value == "Ada"
    assert window.opened.row_version == 7
    assert window.field_label == "Name"
    assert editors.open_editors() == ("w1",)


def test_save_commits_exactly_the_one_field_against_the_loaded_version() -> None:
    editors = FieldEditors()
    window = _open(editors)
    editors.edit_value(window.opened.window_key, "Grace")
    commit = editors.request_save(window.opened.window_key)
    assert isinstance(commit, CommitSingleField)
    assert commit.payload == {"mentorName": "Grace", "rowVersion": 7}


def test_computed_field_is_refused_in_the_edit_forms_own_words() -> None:
    editors = FieldEditors()
    outcome = open_from_double_click(editors, "w1", "mentor", SESSIONS_FIELD, RECORD)
    assert isinstance(outcome, FieldEditRefused)
    read_only = classify_read_only(
        "mentor", "mentorSessionCount", "Sessions held", visibility_hints={"computed": True}
    )
    assert read_only is not None
    # One explanation, both gestures: the double-click refusal speaks exactly
    # the flattened words the edit form's click shows.
    assert outcome.reason == field_edit_reason(read_only)
    assert editors.open_editors() == ()


def test_permission_blocked_field_names_the_grant_and_the_granter() -> None:
    editors = FieldEditors()
    outcome = open_from_double_click(
        editors, "w1", "mentor", RATE_FIELD, RECORD, permission_block=RATE_BLOCK
    )
    assert isinstance(outcome, FieldEditRefused)
    assert "Edit mentor rates" in outcome.reason
    assert "your program administrator" in outcome.reason


def test_second_double_click_on_the_same_field_offers_the_switch() -> None:
    editors = FieldEditors()
    _open(editors, "w1")
    outcome = open_from_double_click(editors, "w2", "mentor", NAME_FIELD, RECORD)
    assert isinstance(outcome, FieldEditSwitch)
    assert outcome.existing_window_key == "w1"
    message = switch_offer_message("Name")
    assert "'Name'" in message.what_happened
    assert SWITCH_TO_WINDOW_LABEL in message.what_next


# --- The control (never a lesser editor than the full form) ---------------------------


def test_rich_text_field_hosts_the_one_rich_text_control() -> None:
    editors = FieldEditors()
    window = _open(editors, spec=NOTES_FIELD)
    assert window.control is RICH_TEXT_CONTROL


def test_reference_field_hosts_the_standard_lookup_control() -> None:
    assert window_control("reference") is LOOKUP_CONTROL
    editors = FieldEditors()
    window = _open(editors, spec=PROGRAM_FIELD)
    assert window.control is LOOKUP_CONTROL


def test_every_other_type_gets_its_standard_typed_input() -> None:
    assert window_control("string") == TypedInput("string")
    assert window_control("date") == TypedInput("date")
