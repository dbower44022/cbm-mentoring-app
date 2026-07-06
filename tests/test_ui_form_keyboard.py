"""Form keyboard standard gate: tab cycle, initial focus, key dispatch (WTK-075, WTK-084)."""

from __future__ import annotations

from mentorapp.ui.edit_form import (
    EDIT_FORM_SCREEN,
    SAVE_SHORTCUT,
    CommitChanges,
    DirtyLeaveGuard,
    EditForm,
    LeaveAllowed,
    NothingToSave,
    first_editable_field,
)
from mentorapp.ui.field_edit_window import FIELD_EDIT_FRAME
from mentorapp.ui.form_keyboard import (
    ENTER_KEY,
    ESCAPE_KEY,
    FORM_KEYBOARD_MODEL,
    SHIFT_TAB_KEY,
    TAB_KEY,
    ActivateFocusedControl,
    form_key,
    initial_focus,
    next_tab_stop,
    tab_order,
)
from mentorapp.ui.readonly_fields import (
    EditableField,
    PermissionBlock,
    ReadOnlyField,
    edit_form_disposition,
)

RATE_BLOCK = PermissionBlock("Edit mentor rates", "your program administrator")

# Name, Phone, Notes editable; Sessions held computed; Rate permission-blocked
# — one of each REQ-039 read-only kind the tab cycle must skip.
SCHEMA_FIELDS = (
    {"fieldName": "mentorName", "fieldLabel": "Name", "visibilityHints": None},
    {"fieldName": "mentorPhone", "fieldLabel": "Phone", "visibilityHints": None},
    {
        "fieldName": "mentorSessionCount",
        "fieldLabel": "Sessions held",
        "visibilityHints": {"computed": True},
    },
    {"fieldName": "mentorRate", "fieldLabel": "Rate", "visibilityHints": None},
    {"fieldName": "mentorNotes", "fieldLabel": "Notes", "visibilityHints": None},
)

RECORD = {
    "mentorID": "m-1",
    "mentorName": "Ada",
    "mentorPhone": "(555) 010-2000",
    "mentorSessionCount": 12,
    "mentorRate": 45,
    "mentorNotes": "",
    "rowVersion": 7,
}


def _dispositions() -> tuple[EditableField | ReadOnlyField, ...]:
    return edit_form_disposition(
        "mentor", SCHEMA_FIELDS, permission_blocks={"mentorRate": RATE_BLOCK}
    )


def _form() -> EditForm:
    return EditForm.from_disposition("mentor", _dispositions(), RECORD)


# --- The restricted tab cycle -------------------------------------------------------


def test_tab_stops_only_on_editable_fields_in_display_order() -> None:
    stops = tab_order(_dispositions())
    # Computed (Sessions held) and permission-blocked (Rate) fields render in
    # place but take no stop — REQ-038's restriction, over REQ-039's kinds.
    assert [s.field_name for s in stops] == ["mentorName", "mentorPhone", "mentorNotes"]


def test_tab_and_shift_tab_walk_the_wrapping_cycle() -> None:
    dispositions = _dispositions()
    assert next_tab_stop(dispositions, "mentorName").field_name == "mentorPhone"
    # Forward past Rate's dead stop: Phone -> Notes, skipping both read-onlys.
    assert next_tab_stop(dispositions, "mentorPhone").field_name == "mentorNotes"
    # The cycle wraps: there is no other legitimate stop to fall off to.
    assert next_tab_stop(dispositions, "mentorNotes").field_name == "mentorName"
    assert next_tab_stop(dispositions, "mentorName", backwards=True).field_name == "mentorNotes"


def test_tab_from_outside_the_cycle_reenters_at_the_edge() -> None:
    dispositions = _dispositions()
    # Focus on the Save row, a label, or a read-only element is outside the
    # cycle: Tab re-enters at the first stop, Shift+Tab at the last.
    assert next_tab_stop(dispositions, None).field_name == "mentorName"
    assert next_tab_stop(dispositions, "mentorSessionCount").field_name == "mentorName"
    assert next_tab_stop(dispositions, None, backwards=True).field_name == "mentorNotes"


def test_fully_read_only_form_has_no_stops() -> None:
    computed_only = (
        {
            "fieldName": "mentorSessionCount",
            "fieldLabel": "Sessions held",
            "visibilityHints": {"computed": True},
        },
    )
    dispositions = edit_form_disposition("mentor", computed_only)
    assert tab_order(dispositions) == ()
    assert initial_focus(dispositions) is None
    assert next_tab_stop(dispositions, None) is None


# --- Initial focus ------------------------------------------------------------------


def test_initial_focus_is_the_first_tab_stop_and_agrees_with_edit_form() -> None:
    dispositions = _dispositions()
    focus = initial_focus(dispositions)
    assert focus is not None and focus.field_name == "mentorName"
    # One rule, two homes: the frame's declared initial focus resolves to the
    # exact field edit_form's own resolver answers.
    assert focus == first_editable_field(dispositions)


# --- Key dispatch, wired to the dirty guard -----------------------------------------


def test_ctrl_s_on_a_clean_form_is_a_declared_no_op() -> None:
    assert isinstance(form_key(_form(), SAVE_SHORTCUT), NothingToSave)


def test_ctrl_s_on_a_dirty_form_commits_the_minimal_patch() -> None:
    form = _form()
    form.edit("mentorPhone", "(555) 010-9999")
    outcome = form_key(form, SAVE_SHORTCUT)
    assert isinstance(outcome, CommitChanges)
    assert outcome.changes == {"mentorPhone": "(555) 010-9999"}
    assert outcome.row_version == 7


def test_escape_runs_the_dirty_guard_not_a_bypass() -> None:
    form = _form()
    assert isinstance(form_key(form, ESCAPE_KEY), LeaveAllowed)
    form.edit("mentorName", "Ada L.")
    guard = form_key(form, ESCAPE_KEY)
    assert isinstance(guard, DirtyLeaveGuard)
    assert guard.dirty_fields == ("mentorName",)
    # The guard stood: the edit survives until an explicit save or discard.
    assert form.value_of("mentorName") == "Ada L."


def test_enter_never_submits_a_multi_field_form() -> None:
    form = _form()
    form.edit("mentorName", "Ada L.")
    outcome = form_key(form, ENTER_KEY)
    assert isinstance(outcome, ActivateFocusedControl)
    # Nothing saved, nothing reverted — Enter acted on the focused control only.
    assert form.dirty_fields() == ("mentorName",)


def test_other_keys_are_not_the_forms() -> None:
    assert form_key(_form(), "F5") is None


# --- The declared model never drifts from the owning frames -------------------------


def test_model_reads_the_owning_frames_facts() -> None:
    assert FORM_KEYBOARD_MODEL.save == SAVE_SHORTCUT == "Ctrl+S"
    assert FORM_KEYBOARD_MODEL.escape_full_form == EDIT_FORM_SCREEN.escape == "requestLeave"
    assert FORM_KEYBOARD_MODEL.escape_per_field_window == FIELD_EDIT_FRAME.escape == "cancel"
    assert FORM_KEYBOARD_MODEL.initial_focus == EDIT_FORM_SCREEN.initial_focus
    assert FORM_KEYBOARD_MODEL.enter == "activateFocusedControl"


# --- WTK-084 verification pass: the REQ-038 lines WTK-075 left unexercised ----------


def test_system_fields_take_no_tab_stop_either() -> None:
    # The third REQ-039 kind in the cycle: name-detected system fields (the
    # entity's own ID, structural columns) — the registry seed keeps them out
    # of GET /schema, but the cycle must skip them even if a schema hands
    # them over, same as computed and permission-blocked.
    with_system = (
        {"fieldName": "mentorID", "fieldLabel": "ID", "visibilityHints": None},
        *SCHEMA_FIELDS,
        {"fieldName": "modifiedAt", "fieldLabel": "Modified", "visibilityHints": None},
    )
    dispositions = edit_form_disposition(
        "mentor", with_system, permission_blocks={"mentorRate": RATE_BLOCK}
    )
    stops = tab_order(dispositions)
    assert [s.field_name for s in stops] == ["mentorName", "mentorPhone", "mentorNotes"]
    # Initial focus skips the leading system field too.
    assert initial_focus(dispositions).field_name == "mentorName"


def test_shift_tab_walks_backwards_mid_cycle() -> None:
    dispositions = _dispositions()
    # Backwards past Rate's dead stop: Notes -> Phone, skipping both read-onlys.
    back_from_notes = next_tab_stop(dispositions, "mentorNotes", backwards=True)
    assert back_from_notes.field_name == "mentorPhone"
    assert next_tab_stop(dispositions, "mentorPhone", backwards=True).field_name == "mentorName"


def test_shift_tab_from_outside_the_cycle_reenters_at_the_last_stop() -> None:
    dispositions = _dispositions()
    # Focus on a read-only element is outside the cycle in both directions:
    # Shift+Tab re-enters at the LAST stop, mirroring Tab's first-stop rule.
    assert (
        next_tab_stop(dispositions, "mentorSessionCount", backwards=True).field_name
        == "mentorNotes"
    )
    assert next_tab_stop(dispositions, "mentorRate", backwards=True).field_name == "mentorNotes"
    # Forward from the permission-blocked kind (the existing gate only walks
    # forward from the computed one).
    assert next_tab_stop(dispositions, "mentorRate").field_name == "mentorName"


def test_single_stop_cycle_wraps_to_itself() -> None:
    one_editable = (
        {
            "fieldName": "mentorSessionCount",
            "fieldLabel": "Sessions held",
            "visibilityHints": {"computed": True},
        },
        {"fieldName": "mentorNotes", "fieldLabel": "Notes", "visibilityHints": None},
    )
    dispositions = edit_form_disposition("mentor", one_editable)
    # One legitimate stop: the wrapping cycle keeps focus on it — Tab never
    # escapes to Save, a label, or the read-only field, in either direction.
    assert initial_focus(dispositions).field_name == "mentorNotes"
    assert next_tab_stop(dispositions, "mentorNotes").field_name == "mentorNotes"
    back_from_only_stop = next_tab_stop(dispositions, "mentorNotes", backwards=True)
    assert back_from_only_stop.field_name == "mentorNotes"


def test_tab_keys_are_not_the_dispatchers() -> None:
    # Tab/Shift+Tab resolve through next_tab_stop with the shell's focus
    # position — form_key deliberately doesn't track focus, so both fall out
    # as not-the-form's-key rather than a second tab-order home.
    form = _form()
    assert form_key(form, TAB_KEY) is None
    assert form_key(form, SHIFT_TAB_KEY) is None


def test_enter_on_a_clean_form_stays_clean() -> None:
    form = _form()
    assert isinstance(form_key(form, ENTER_KEY), ActivateFocusedControl)
    assert not form.is_dirty()


def test_ctrl_s_after_retyping_the_original_is_nothing_to_save() -> None:
    # Dirty is a value comparison, never a touched flag: a field typed back
    # to its original leaves the keyboard Save a declared no-op, no PATCH.
    form = _form()
    form.edit("mentorPhone", "(555) 010-9999")
    form.edit("mentorPhone", "(555) 010-2000")
    assert isinstance(form_key(form, SAVE_SHORTCUT), NothingToSave)


def test_escape_after_discard_allows_leave() -> None:
    form = _form()
    form.edit("mentorName", "Ada L.")
    assert isinstance(form_key(form, ESCAPE_KEY), DirtyLeaveGuard)
    # The user confirmed the guard: the same Escape now passes, and the
    # abandoned edit is gone from the controls.
    form.discard()
    assert isinstance(form_key(form, ESCAPE_KEY), LeaveAllowed)
    assert form.value_of("mentorName") == "Ada"


def test_model_declares_the_tab_slots() -> None:
    assert FORM_KEYBOARD_MODEL.tab == "nextEditableField"
    assert FORM_KEYBOARD_MODEL.shift_tab == "previousEditableField"
