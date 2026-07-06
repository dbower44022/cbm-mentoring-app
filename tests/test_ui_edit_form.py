"""Full-screen edit form design gate: frame, dirty lifecycle, revert, commit (WTK-066)."""

from __future__ import annotations

import pytest

from mentorapp.ui.edit_form import (
    EDIT_FORM_SCREEN,
    EDIT_RECORD,
    CommitChanges,
    DirtyLeaveGuard,
    EditForm,
    LeaveAllowed,
    NotEditableError,
    NothingToSave,
    first_editable_field,
    leave_warning,
)
from mentorapp.ui.readonly_fields import PermissionBlock, edit_form_disposition

RATE_BLOCK = PermissionBlock("Edit mentor rates", "your program administrator")

SCHEMA_FIELDS = (
    {"fieldName": "mentorName", "fieldLabel": "Name", "visibilityHints": None},
    {"fieldName": "mentorPhone", "fieldLabel": "Phone", "visibilityHints": None},
    {
        "fieldName": "mentorSessionCount",
        "fieldLabel": "Sessions held",
        "visibilityHints": {"computed": True},
    },
    {"fieldName": "mentorRate", "fieldLabel": "Rate", "visibilityHints": None},
)

RECORD = {
    "mentorID": "m-1",
    "mentorName": "Ada",
    "mentorPhone": "(555) 010-2000",
    "mentorSessionCount": 12,
    "mentorRate": 45,
    "rowVersion": 7,
}


def _form() -> EditForm:
    dispositions = edit_form_disposition(
        "mentor", SCHEMA_FIELDS, permission_blocks={"mentorRate": RATE_BLOCK}
    )
    return EditForm.from_disposition("mentor", dispositions, RECORD)


# --- The frame (REQ-032) -------------------------------------------------------------


def test_the_form_is_full_screen_and_matches_the_read_view_scaled_up() -> None:
    assert EDIT_FORM_SCREEN.presentation == "fullScreen"
    assert EDIT_FORM_SCREEN.field_positions == "matchesReadView"
    assert EDIT_FORM_SCREEN.control_scale == "scaledUpForEditControls"


def test_save_is_large_and_cancel_reverts() -> None:
    assert EDIT_FORM_SCREEN.save.prominence == "large"
    assert EDIT_FORM_SCREEN.save.shortcut == "Ctrl+S"
    # Revert, not close: the form stays open on the restored values.
    assert EDIT_FORM_SCREEN.cancel.behavior == "revertToOriginal"


def test_escape_requests_leave_and_focus_starts_on_the_first_editable_field() -> None:
    assert EDIT_FORM_SCREEN.escape == "requestLeave"
    assert EDIT_FORM_SCREEN.initial_focus == "firstEditableField"


def test_the_edit_action_is_modifying_and_single_row() -> None:
    assert EDIT_RECORD.classification == "modifying"
    assert EDIT_RECORD.selection_contract == "single"


def test_first_editable_field_skips_read_only_and_none_means_all_read_only() -> None:
    dispositions = edit_form_disposition(
        "mentor",
        # A leading system field renders read-only in place; focus skips it.
        (
            {"fieldName": "mentorID", "fieldLabel": "Mentor ID", "visibilityHints": None},
            *SCHEMA_FIELDS,
        ),
        permission_blocks={"mentorRate": RATE_BLOCK},
    )
    focus = first_editable_field(dispositions)
    assert focus is not None
    assert focus.field_name == "mentorName"
    all_blocked = edit_form_disposition(
        "mentor",
        SCHEMA_FIELDS,
        permission_blocks={
            "mentorName": RATE_BLOCK,
            "mentorPhone": RATE_BLOCK,
            "mentorRate": RATE_BLOCK,
        },
    )
    assert first_editable_field(all_blocked) is None


# --- Dirty tracking is a value comparison --------------------------------------------


def test_editing_marks_dirty_and_retyping_the_original_is_clean_again() -> None:
    form = _form()
    assert not form.is_dirty()
    form.edit("mentorName", "Grace")
    assert form.dirty_fields() == ("mentorName",)
    form.edit("mentorName", "Ada")
    assert not form.is_dirty()


def test_dirty_fields_come_back_in_display_order() -> None:
    form = _form()
    form.edit("mentorPhone", "(555) 010-9999")
    form.edit("mentorName", "Grace")
    assert form.dirty_fields() == ("mentorName", "mentorPhone")


def test_only_disposition_editable_fields_take_edits() -> None:
    form = _form()
    with pytest.raises(NotEditableError):
        form.edit("mentorSessionCount", 99)  # computed
    with pytest.raises(NotEditableError):
        form.edit("mentorRate", 60)  # permission-blocked
    with pytest.raises(NotEditableError):
        form.edit("rowVersion", 8)  # structural, never in the disposition


# --- The leave guard (warn while dirty, never silently) ------------------------------


def test_leaving_clean_needs_no_ceremony() -> None:
    assert isinstance(_form().request_leave(), LeaveAllowed)


def test_leaving_dirty_warns_and_names_the_fields() -> None:
    form = _form()
    form.edit("mentorName", "Grace")
    form.edit("mentorPhone", "(555) 010-9999")
    guard = form.request_leave()
    assert isinstance(guard, DirtyLeaveGuard)
    assert guard.dirty_fields == ("mentorName", "mentorPhone")
    assert "2 fields have" in guard.warning.why
    assert "Name" in guard.warning.why
    assert "Phone" in guard.warning.why
    assert guard.warning.what_happened
    assert guard.warning.what_next


def test_the_warning_speaks_singular_for_one_field() -> None:
    warning = leave_warning(["Name"])
    assert "1 field has" in warning.why


def test_discard_after_the_guard_allows_the_leave() -> None:
    form = _form()
    form.edit("mentorName", "Grace")
    assert isinstance(form.request_leave(), DirtyLeaveGuard)
    form.discard()
    assert isinstance(form.request_leave(), LeaveAllowed)
    assert form.value_of("mentorName") == "Ada"


# --- Cancel reverts to the original values -------------------------------------------


def test_cancel_restores_originals_and_the_form_stays_open() -> None:
    form = _form()
    form.edit("mentorName", "Grace")
    form.edit("mentorPhone", "(555) 010-9999")
    reverted = form.cancel()
    assert reverted == {"mentorName": "Ada", "mentorPhone": "(555) 010-2000"}
    assert not form.is_dirty()
    assert form.value_of("mentorName") == "Ada"
    # Still open and editable after the revert.
    form.edit("mentorName", "Grace")
    assert form.dirty_fields() == ("mentorName",)


def test_cancel_when_clean_changes_nothing() -> None:
    assert _form().cancel() == {}


# --- Save: the minimal PATCH, and rebasing after it lands -----------------------------


def test_save_when_clean_is_a_declared_no_op() -> None:
    assert isinstance(_form().request_save(), NothingToSave)


def test_save_commits_exactly_the_changed_fields_against_the_loaded_version() -> None:
    form = _form()
    form.edit("mentorName", "Grace")
    form.edit("mentorPhone", "(555) 010-2000")  # unchanged value: not in the PATCH
    commit = form.request_save()
    assert isinstance(commit, CommitChanges)
    assert commit.changes == {"mentorName": "Grace"}
    assert commit.row_version == 7


def test_saved_rebases_so_cancel_and_guard_answer_from_the_committed_values() -> None:
    form = _form()
    form.edit("mentorName", "Grace")
    form.saved({**RECORD, "mentorName": "Grace", "rowVersion": 8})
    assert not form.is_dirty()
    assert isinstance(form.request_leave(), LeaveAllowed)
    assert form.row_version == 8
    # The next edit is relative to the committed base, not the loaded one.
    form.edit("mentorName", "Ada")
    commit = form.request_save()
    assert isinstance(commit, CommitChanges)
    assert commit.changes == {"mentorName": "Ada"}
    assert commit.row_version == 8
