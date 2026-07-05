"""Record preview & pop-out design gate: docked preview, pop-outs, sync (WTK-021)."""

from __future__ import annotations

import pytest

from mentorapp.storage import SELECTION_CONTRACTS
from mentorapp.ui import (
    NO_ROW_FOCUSED,
    OPEN_RECORD_PREVIEW,
    POP_OUT_HAS_NAVIGATION,
    POP_OUT_HEADER_RIGHT,
    POP_OUT_RECORD,
    RECORD_PREVIEW,
    PanelAction,
    RecordRef,
    RecordWindows,
    UnknownWindowError,
    single_row_required_message,
)

MENTEE_1 = RecordRef("mentee", "m-1", "Ada Lovelace")
MENTEE_2 = RecordRef("mentee", "m-2", "Grace Hopper")
SESSION_1 = RecordRef("session", "s-1", "Intro session")


# --- The docked preview pane (REQ-012) -------------------------------------------


def test_preview_pane_is_read_only_with_the_two_edit_paths() -> None:
    assert RECORD_PREVIEW.read_optimized is True
    # Declared impossibility: previews NEVER host edit controls.
    assert RECORD_PREVIEW.edit_controls is False
    assert RECORD_PREVIEW.edit_paths == ("editAction", "perFieldDoubleClick")
    # The standard's implementer's choice, exercised: right dock.
    assert RECORD_PREVIEW.dock_position == "right"
    assert RECORD_PREVIEW.docked_when == "windowSizeAllows"


def test_docked_preview_follows_the_focused_row_live() -> None:
    windows = RecordWindows()
    assert windows.focus_row(MENTEE_1).record == MENTEE_1
    assert windows.focus_row(MENTEE_2).record == MENTEE_2
    assert windows.preview().record == MENTEE_2


def test_no_focus_previews_an_educate_notice_never_a_blank_pane() -> None:
    windows = RecordWindows()
    content = windows.preview()
    assert content.record is None
    assert content.notice == NO_ROW_FOCUSED
    windows.focus_row(MENTEE_1)
    cleared = windows.focus_row(None)
    assert cleared.record is None
    assert cleared.notice == NO_ROW_FOCUSED


# --- The two declared actions ------------------------------------------------------


def test_actions_declare_single_selection_and_safe_classification() -> None:
    for action in (OPEN_RECORD_PREVIEW, POP_OUT_RECORD):
        assert action.selection_contract == "single"
        assert action.selection_contract in SELECTION_CONTRACTS
        assert action.classification == "safe"
    assert OPEN_RECORD_PREVIEW.key == "OpenRecordPreview"
    assert POP_OUT_RECORD.key == "PopOutRecord"


def test_action_declaration_rejects_contracts_outside_the_shared_vocabulary() -> None:
    with pytest.raises(ValueError):
        PanelAction("Bad", "Bad", "some", "safe")


def test_invalid_invocation_explains_instead_of_hiding() -> None:
    none_selected = single_row_required_message(POP_OUT_RECORD, 0)
    assert none_selected.what_happened == "'Pop out' didn't run."
    assert "none is selected" in none_selected.why
    many_selected = single_row_required_message(POP_OUT_RECORD, 7)
    assert "7 rows are selected" in many_selected.why
    assert "single row" in many_selected.what_next


# --- Pop-out windows (REQ-012) -----------------------------------------------------


def test_pop_out_opens_a_real_browser_window_pinned_to_its_record() -> None:
    windows = RecordWindows()
    result = windows.pop_out(MENTEE_1)
    assert result.notice is None
    assert result.window.kind == "browserWindow"
    assert result.window.record == MENTEE_1
    # Pinned: focus changes in the grid never re-target a pop-out.
    windows.focus_row(MENTEE_2)
    assert windows.open_pop_outs()[0].record == MENTEE_1
    assert windows.preview().record == MENTEE_2


def test_multiple_pop_outs_coexist_across_records() -> None:
    windows = RecordWindows()
    keys = {windows.pop_out(r).window.window_key for r in (MENTEE_1, MENTEE_2, SESSION_1)}
    assert len(keys) == 3
    assert len(windows.open_pop_outs()) == 3


def test_popping_out_an_open_record_raises_the_existing_window_with_notice() -> None:
    windows = RecordWindows()
    first = windows.pop_out(MENTEE_1)
    # Same identity under a stale title still finds the live window.
    again = windows.pop_out(RecordRef("mentee", "m-1", "A. Lovelace (old title)"))
    assert again.window.window_key == first.window.window_key
    assert again.notice is not None
    assert "already open" in again.notice.what_happened
    assert len(windows.open_pop_outs()) == 1


def test_close_removes_the_window_and_guards_unknown_keys() -> None:
    windows = RecordWindows()
    key = windows.pop_out(MENTEE_1).window.window_key
    windows.close_pop_out(key)
    assert windows.open_pop_outs() == ()
    with pytest.raises(UnknownWindowError):
        windows.close_pop_out(key)


def test_pop_outs_survive_main_window_close() -> None:
    windows = RecordWindows()
    windows.focus_row(MENTEE_1)
    windows.pop_out(MENTEE_2)
    survivors = windows.main_window_closed()
    assert [w.record for w in survivors] == [MENTEE_2]
    # The docked preview died with its window.
    assert windows.preview().record is None


def test_pop_out_header_is_the_standard_header_minus_navigation() -> None:
    assert POP_OUT_HEADER_RIGHT == ("notificationBell", "help", "accountMenu")
    assert POP_OUT_HAS_NAVIGATION is False


# --- Same-user cross-window sync -----------------------------------------------------


def test_record_saved_fans_out_to_every_surface_showing_it() -> None:
    windows = RecordWindows()
    windows.focus_row(MENTEE_1)
    pinned = windows.pop_out(MENTEE_1).window.window_key
    windows.pop_out(MENTEE_2)
    fanout = windows.record_saved(MENTEE_1)
    assert fanout.docked is True
    assert fanout.pop_out_keys == (pinned,)


def test_record_saved_touches_nothing_when_no_surface_shows_it() -> None:
    windows = RecordWindows()
    windows.focus_row(MENTEE_1)
    windows.pop_out(MENTEE_2)
    fanout = windows.record_saved(SESSION_1)
    assert fanout.docked is False
    assert fanout.pop_out_keys == ()
