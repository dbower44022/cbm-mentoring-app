"""Per-field edit window & single-field write design (REQ-035, WTK-059).

Records are wire-shaped dicts (``serialize_record`` output); the controller
is pure, so no database is needed — the write contract's PATCH/409 mechanics
themselves are covered by ``test_api_processes``, and the shared conflict
resolver by ``test_api_edit_safety``.
"""

from __future__ import annotations

from typing import Any

import pytest

from mentorapp.api import (
    FIELD_EDIT_TRIGGER,
    FIELD_EDIT_WINDOW,
    AlreadyCurrent,
    CommitSingleField,
    DirtyWindowGuard,
    FieldEditOpened,
    FieldEditors,
    FieldEditRefused,
    FieldEditSwitch,
    ManualMerge,
    NothingToSave,
    RetrySave,
    SaveNotice,
    single_field_patch,
)
from mentorapp.api.edit_safety import CloseAllowed, UnknownEditorError

ENTITY = "postalCode"
RECORD_ID = "0197-example"


def _record(row_version: int, **fields: Any) -> dict[str, Any]:
    """A wire-shaped record: business fields plus the structural columns that
    ALWAYS differ between a loaded copy and a 409 body."""
    return {
        "postalCodeID": RECORD_ID,
        "rowVersion": row_version,
        "modifiedAt": f"2026-07-05T00:00:0{row_version}Z",
        "modifiedBy": "someone-else",
        **fields,
    }


def _open(editors: FieldEditors, key: str = "w1", **fields: Any) -> FieldEditOpened:
    outcome = editors.open(key, ENTITY, "city", _record(1, city="Springfield", **fields))
    assert isinstance(outcome, FieldEditOpened)
    return outcome


# --- The declared window ------------------------------------------------------------


def test_window_declares_small_editor_with_own_save_and_cancel() -> None:
    # REQ-035's shape as declared properties: the shell can never inflate the
    # window into a full form or route Save anywhere but the one-field PATCH.
    assert FIELD_EDIT_WINDOW.kind == "smallWindow"
    assert FIELD_EDIT_WINDOW.trigger == FIELD_EDIT_TRIGGER == "doubleClick"
    assert (FIELD_EDIT_WINDOW.save_label, FIELD_EDIT_WINDOW.cancel_label) == ("Save", "Cancel")
    assert FIELD_EDIT_WINDOW.commits == "singleFieldPatch"


def test_single_field_patch_is_one_field_plus_row_version() -> None:
    assert single_field_patch("city", "Alexandria", 4) == {
        "city": "Alexandria",
        "rowVersion": 4,
    }


# --- Opening ------------------------------------------------------------------------


def test_open_bases_the_editor_on_the_loaded_record() -> None:
    opened = _open(FieldEditors())
    assert opened == FieldEditOpened("w1", ENTITY, RECORD_ID, "city", "Springfield", 1)


def test_structural_and_key_fields_are_refused_with_a_reason() -> None:
    editors = FieldEditors()
    for name in ("rowVersion", "modifiedAt", "postalCodeID"):
        outcome = editors.open("w1", ENTITY, name, _record(1))
        assert isinstance(outcome, FieldEditRefused)
        assert outcome.reason
    assert editors.open_editors() == ()


def test_caller_known_read_only_field_is_refused_with_its_explanation() -> None:
    outcome = FieldEditors().open(
        "w1", ENTITY, "city", _record(1), read_only_reason="Cities sync from the CRM."
    )
    assert outcome == FieldEditRefused("city", "Cities sync from the CRM.")


def test_second_window_on_the_same_field_switches_to_the_existing_one() -> None:
    editors = FieldEditors()
    _open(editors, "w1")
    outcome = editors.open("w2", ENTITY, "city", _record(1, city="Springfield"))
    assert outcome == FieldEditSwitch("w1", ENTITY, RECORD_ID, "city")
    assert editors.open_editors() == ("w1",)


def test_reopening_from_the_owning_window_is_idempotent() -> None:
    editors = FieldEditors()
    _open(editors, "w1")
    assert isinstance(editors.open("w1", ENTITY, "city", _record(1)), FieldEditOpened)


def test_different_fields_of_one_record_may_be_edited_at_once() -> None:
    # Single-field writes are disjoint by construction; overlap resolves via
    # the standard 409 path instead of being forbidden up front.
    editors = FieldEditors()
    _open(editors, "w1", state="VA")
    outcome = editors.open("w2", ENTITY, "state", _record(1, city="Springfield", state="VA"))
    assert isinstance(outcome, FieldEditOpened)
    assert editors.open_editors() == ("w1", "w2")


# --- Save ---------------------------------------------------------------------------


def test_save_commits_exactly_the_one_field() -> None:
    editors = FieldEditors()
    _open(editors)
    editors.edit_value("w1", "Alexandria")
    outcome = editors.request_save("w1")
    assert outcome == CommitSingleField(
        "w1", ENTITY, RECORD_ID, {"city": "Alexandria", "rowVersion": 1}
    )


def test_save_with_the_value_back_at_base_sends_nothing_and_closes() -> None:
    editors = FieldEditors()
    _open(editors)
    editors.edit_value("w1", "Alexandria")
    editors.edit_value("w1", "Springfield")  # typed, then typed back
    assert editors.request_save("w1") == NothingToSave("w1")
    assert editors.open_editors() == ()


def test_landed_save_closes_the_window_and_broadcasts_the_standard_notice() -> None:
    editors = FieldEditors()
    _open(editors)
    editors.edit_value("w1", "Alexandria")
    editors.request_save("w1")
    notice = editors.save_succeeded("w1", new_row_version=2)
    assert notice == SaveNotice(ENTITY, RECORD_ID, 2, "updated")
    assert editors.open_editors() == ()


# --- The concurrency check (REQ-035 acceptance) --------------------------------------


def test_conflict_on_an_untouched_field_auto_retries_invisibly() -> None:
    editors = FieldEditors()
    _open(editors, state="VA")
    editors.edit_value("w1", "Alexandria")
    current = _record(2, city="Springfield", state="MD")  # they changed state
    outcome = editors.save_conflicted("w1", current)
    assert outcome == RetrySave(changes={"city": "Alexandria"}, row_version=2)
    # The 409 body is the new base: the retry commits against version 2.
    retry = editors.request_save("w1")
    assert isinstance(retry, CommitSingleField)
    assert retry.payload == {"city": "Alexandria", "rowVersion": 2}


def test_conflict_on_the_same_field_surfaces_the_standard_walk_through() -> None:
    editors = FieldEditors()
    _open(editors)
    editors.edit_value("w1", "Alexandria")
    current = _record(2, city="Arlington")  # they changed THIS field
    outcome = editors.save_conflicted("w1", current)
    assert isinstance(outcome, ManualMerge)
    assert len(outcome.conflicts) == 1
    conflict = outcome.conflicts[0]
    assert (conflict.base_value, conflict.your_value, conflict.their_value) == (
        "Springfield",
        "Alexandria",
        "Arlington",
    )
    assert editors.open_editors() == ("w1",)


def test_conflict_where_they_already_wrote_the_same_value_closes_the_window() -> None:
    editors = FieldEditors()
    _open(editors)
    editors.edit_value("w1", "Alexandria")
    outcome = editors.save_conflicted("w1", _record(2, city="Alexandria"))
    assert outcome == AlreadyCurrent(row_version=2)
    assert editors.open_editors() == ()


# --- Cancel & close ------------------------------------------------------------------


def test_cancel_discards_without_ceremony() -> None:
    editors = FieldEditors()
    _open(editors)
    editors.edit_value("w1", "Alexandria")
    editors.cancel("w1")
    assert editors.open_editors() == ()


def test_non_cancel_close_over_an_edited_value_raises_the_dirty_guard() -> None:
    editors = FieldEditors()
    _open(editors)
    editors.edit_value("w1", "Alexandria")
    assert editors.request_close("w1") == DirtyWindowGuard("w1", ("city",))
    assert editors.open_editors() == ("w1",)
    editors.edit_value("w1", "Springfield")  # back at base: clean again
    assert editors.request_close("w1") == CloseAllowed("w1")
    assert editors.open_editors() == ()


def test_unknown_window_key_is_a_caller_bug() -> None:
    with pytest.raises(UnknownEditorError):
        FieldEditors().request_save("nope")
