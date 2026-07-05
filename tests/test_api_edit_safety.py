"""Concurrency, freshness & edit-safety processes (REQ-013, WTK-024).

Records are wire-shaped dicts (``serialize_record`` output); the resolver
and the fan-out rule are pure, so no database is needed — the write
contract's 409 mechanics themselves are covered by ``test_api_processes``.
"""

from __future__ import annotations

from typing import Any

import pytest

from mentorapp.api import (
    AlreadyCurrent,
    DirtyWindowGuard,
    EditCollisionSwitch,
    EditorWindows,
    ManualMerge,
    RetrySave,
    SaveNotice,
    resolve_concurrent_save_conflict,
    surface_needs_refresh,
)
from mentorapp.api.edit_safety import AUTO_RETRY_LIMIT, CloseAllowed, UnknownEditorError

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


# --- ConcurrentSaveConflictResolution ----------------------------------------------


def test_disjoint_edits_auto_retry_against_fresh_version() -> None:
    loaded = _record(1, city="Springfield", state="VA")
    current = _record(2, city="Springfield", state="MD")  # they changed state
    outcome = resolve_concurrent_save_conflict({"city": "Alexandria"}, loaded, current)
    assert outcome == RetrySave(changes={"city": "Alexandria"}, row_version=2)


def test_structural_columns_never_count_as_their_changes() -> None:
    # Only structural columns differ between the copies: the other save
    # touched nothing the user can edit, so ANY dirty field retries.
    loaded = _record(1, city="Springfield")
    current = _record(2, city="Springfield")
    outcome = resolve_concurrent_save_conflict({"city": "Alexandria"}, loaded, current)
    assert isinstance(outcome, RetrySave)


def test_agreement_is_pruned_not_conflicted() -> None:
    # Both saves set the same value: nothing left to send, editor rebases.
    loaded = _record(1, city="Springfield")
    current = _record(2, city="Alexandria")
    outcome = resolve_concurrent_save_conflict({"city": "Alexandria"}, loaded, current)
    assert outcome == AlreadyCurrent(row_version=2)


def test_overlap_walks_through_base_yours_theirs() -> None:
    loaded = _record(1, city="Springfield", state="VA")
    current = _record(2, city="Richmond", state="VA")
    outcome = resolve_concurrent_save_conflict(
        {"city": "Alexandria", "state": "MD"}, loaded, current
    )
    assert isinstance(outcome, ManualMerge)
    assert outcome.row_version == 2
    # The untouched dirty field rides along for the single follow-up PATCH.
    assert outcome.clean_changes == {"state": "MD"}
    (conflict,) = outcome.conflicts
    assert conflict.field_name == "city"
    assert conflict.base_value == "Springfield"
    assert conflict.your_value == "Alexandria"
    assert conflict.their_value == "Richmond"


def test_exhausted_retries_force_the_walk_through_even_when_disjoint() -> None:
    loaded = _record(1, city="Springfield", state="VA")
    current = _record(5, city="Springfield", state="MD")
    outcome = resolve_concurrent_save_conflict(
        {"city": "Alexandria"}, loaded, current, attempt=AUTO_RETRY_LIMIT + 1
    )
    assert isinstance(outcome, ManualMerge)
    assert outcome.conflicts == ()  # a hot record, not an overlap
    assert outcome.clean_changes == {"city": "Alexandria"}


# --- CrossWindowFreshness -----------------------------------------------------------


NOTICE = SaveNotice(ENTITY, RECORD_ID, 3, "updated")


def test_record_surface_refreshes_only_when_older() -> None:
    shows_it = {"entity_type": ENTITY, "record_id": RECORD_ID}
    assert surface_needs_refresh(NOTICE, **shows_it, row_version=2)
    # The saving window's own echo (and any replay) is a no-op: idempotent.
    assert not surface_needs_refresh(NOTICE, **shows_it, row_version=3)
    assert not surface_needs_refresh(NOTICE, entity_type=ENTITY, record_id="other")


def test_grid_surface_refreshes_for_its_entity_type_only() -> None:
    assert surface_needs_refresh(NOTICE, entity_type=ENTITY)
    assert not surface_needs_refresh(NOTICE, entity_type="mentor")


# --- DirtyWindowGuard + EditCollisionSwitch ----------------------------------------


@pytest.fixture()
def windows() -> EditorWindows:
    return EditorWindows()


def test_second_edit_offers_the_switch_and_opens_no_second_editor(
    windows: EditorWindows,
) -> None:
    windows.begin_edit("win-1", ENTITY, RECORD_ID, 1)
    outcome = windows.begin_edit("win-2", ENTITY, RECORD_ID, 1)
    assert outcome == EditCollisionSwitch("win-1", ENTITY, RECORD_ID)
    assert windows.open_editors() == ("win-1",)
    # A DIFFERENT record edits freely; re-invoking Edit from the owning
    # window itself is idempotent, not a collision.
    assert windows.begin_edit("win-2", ENTITY, "other", 1).window_key == "win-2"
    assert windows.begin_edit("win-1", ENTITY, RECORD_ID, 1).window_key == "win-1"


def test_dirty_window_guard_blocks_until_saved_or_discarded(
    windows: EditorWindows,
) -> None:
    windows.begin_edit("win-1", ENTITY, RECORD_ID, 1)
    windows.field_edited("win-1", "city")
    guard = windows.request_close("win-1")
    assert guard == DirtyWindowGuard("win-1", ("city",))
    assert windows.open_editors() == ("win-1",)  # the guard never closes
    windows.discard_and_close("win-1")
    assert windows.open_editors() == ()


def test_clean_close_needs_no_ceremony(windows: EditorWindows) -> None:
    windows.begin_edit("win-1", ENTITY, RECORD_ID, 1)
    assert windows.request_close("win-1") == CloseAllowed("win-1")
    assert windows.open_editors() == ()
    with pytest.raises(UnknownEditorError):
        windows.request_close("win-1")


def test_save_clears_dirty_rebases_and_broadcasts_a_feed_tuple(
    windows: EditorWindows,
) -> None:
    windows.begin_edit("win-1", ENTITY, RECORD_ID, 1)
    windows.field_edited("win-1", "city")
    notice = windows.save_succeeded("win-1", 2)
    assert notice == SaveNotice(ENTITY, RECORD_ID, 2, "updated")
    # Saving is not closing: the editor stays open, clean, at the new base.
    assert windows.request_close("win-1") == CloseAllowed("win-1")


def test_editor_notice_action_never_clobbers_typing(windows: EditorWindows) -> None:
    windows.begin_edit("win-1", ENTITY, RECORD_ID, 1)
    notice = SaveNotice(ENTITY, RECORD_ID, 2, "updated")
    assert windows.notice_action("win-1", notice) == "refresh"
    windows.rebased("win-1", 2)
    assert windows.notice_action("win-1", notice) == "ignore"  # echo/replay
    windows.field_edited("win-1", "city")
    later = SaveNotice(ENTITY, RECORD_ID, 3, "updated")
    # Dirty editors hold: the stale base surfaces at save as the 409 the
    # conflict resolver owns, never as a refresh that eats edits.
    assert windows.notice_action("win-1", later) == "hold"
    assert windows.notice_action("win-1", SaveNotice(ENTITY, "other", 9, "updated")) == "ignore"
