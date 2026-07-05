"""WTK-043: the grid panel UI design (REQ-016, REQ-020..REQ-031).

REQ-016 — the three stacked regions and the action-bar thirds. REQ-020 — the
search box arms at the 3rd character and shares the server's constants.
REQ-021/022 — two common buttons + one full menu (Help last) serving dropdown
and right-click alike; never-hide explainers; the destructive confirmation.
REQ-024 — one keyboard model, context-disambiguated. REQ-025 — header sort
clicks and the arrow + position badge. REQ-029 — funnel filters mark the view
modified and refusal educates. REQ-023/026 — whole-set counts with the
keep-selection notice; progress bar past a few seconds. REQ-030 — the four
distinguished states with the documented precedence. REQ-031 — restoration
scopes: the view choice long-term, everything else session-only.
"""

from __future__ import annotations

import pytest

from mentorapp.api import grid_surface
from mentorapp.api.grid_surface import MIN_SEARCH_LENGTH, RECENT_SEARCH_LIMIT
from mentorapp.ui.grid_panel import (
    GRID_FRAME,
    GRID_KEYBOARD_MODEL,
    GRID_SEARCH_BOX,
    HELP_ACTION,
    INITIAL_FOCUS,
    RESTORED_DATA_REFRESHES,
    STATE_DATA_SOURCE_ERROR,
    STATE_EMPTY_VIEW,
    STATE_PERMISSION_REFUSAL,
    STATE_RESTORATION,
    STATE_ZERO_FILTERED_SEARCH,
    ColumnFilter,
    ColumnFilterSet,
    GridStateInputs,
    SortModel,
    ViewSelection,
    action_menus,
    binding_for,
    destructive_confirmation,
    header_sort_click,
    invalid_invocation,
    last_view_preference_key,
    progress_display,
    resolve_grid_state,
    row_count_label,
    search_is_live,
)
from mentorapp.ui.record_preview import PanelAction

EDIT = PanelAction(
    key="Edit", label="Edit", selection_contract="single", classification="modifying"
)
REMOVE = PanelAction(
    key="Remove", label="Remove", selection_contract="multiple", classification="destructive"
)
EXPORT = PanelAction(
    key="Export", label="Export", selection_contract="none", classification="safe"
)
ACTIONS = (EDIT, REMOVE, EXPORT)


# --- REQ-016: anatomy ---------------------------------------------------------------


def test_grid_frame_is_three_stacked_regions() -> None:
    assert GRID_FRAME.regions == ("actionBar", "dataTable", "statusBar")


def test_action_bar_thirds_hold_views_search_actions() -> None:
    assert GRID_FRAME.action_bar_left == ("viewSelector", "editViewButton")
    assert GRID_FRAME.action_bar_middle == ("searchBox",)
    assert GRID_FRAME.action_bar_right == ("commonActionButtons", "otherActionsMenu")


# --- REQ-020: the search box ---------------------------------------------------------


def test_search_box_speaks_the_server_constants() -> None:
    assert GRID_SEARCH_BOX.min_length == MIN_SEARCH_LENGTH
    assert GRID_SEARCH_BOX.history_limit == RECENT_SEARCH_LIMIT
    assert GRID_SEARCH_BOX.narrows_view_filters is True
    assert GRID_SEARCH_BOX.scope == "displayedColumns"


def test_search_arms_at_the_third_character() -> None:
    assert not search_is_live("ab")
    assert not search_is_live("  ab  ")
    assert search_is_live("acm")


# --- The view selector & modified flag ----------------------------------------------


def test_modifications_raise_the_flag_and_dedupe() -> None:
    view = ViewSelection("needsFollowUp")
    assert not view.state().is_modified
    view.modify("sort")
    state = view.modify("sort")
    assert state.is_modified
    assert state.modifications == ("sort",)


def test_unknown_modification_kind_is_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="search"):
        ViewSelection("v").modify("search")


def test_selecting_a_view_applies_instantly_and_discards_temporaries() -> None:
    view = ViewSelection("needsFollowUp")
    view.modify("sort")
    state = view.select_view("allEngagements")
    assert state.active_view_key == "allEngagements"
    assert not state.is_modified


def test_saving_as_user_view_keeps_the_settings_and_clears_the_flag() -> None:
    view = ViewSelection("needsFollowUp")
    view.modify("adHocFilter")
    state = view.save_as_user_view("myFollowUp")
    assert state.active_view_key == "myFollowUp"
    assert not state.is_modified


# --- REQ-025: sorting ----------------------------------------------------------------


def test_click_is_sole_sort_and_repeat_toggles() -> None:
    sort = SortModel()
    keys = sort.click("mentorName")
    assert [(k.field_name, k.descending) for k in keys] == [("mentorName", False)]
    assert sort.click("mentorName")[0].descending is True
    # Clicking another field replaces the whole sort, ascending again.
    keys = sort.click("startedAt")
    assert [(k.field_name, k.descending) for k in keys] == [("startedAt", False)]


def test_shift_click_appends_toggles_then_removes() -> None:
    sort = SortModel()
    sort.click("mentorName")
    sort.shift_click("startedAt")
    assert [k.field_name for k in sort.sort_keys()] == ["mentorName", "startedAt"]
    assert sort.shift_click("startedAt")[1].descending is True
    assert [k.field_name for k in sort.shift_click("startedAt")] == ["mentorName"]


def test_sorted_headers_carry_arrow_and_position_badge() -> None:
    sort = SortModel()
    sort.click("mentorName")
    sort.shift_click("startedAt")
    sort.shift_click("startedAt")
    assert (sort.badge_for("mentorName").direction, sort.badge_for("mentorName").position) == (
        "asc",
        1,
    )
    assert (sort.badge_for("startedAt").direction, sort.badge_for("startedAt").position) == (
        "desc",
        2,
    )
    assert sort.badge_for("status") is None


def test_header_sorting_marks_the_view_modified() -> None:
    sort, view = SortModel(), ViewSelection("needsFollowUp")
    header_sort_click(sort, view, "mentorName")
    assert view.state().modifications == ("sort",)
    header_sort_click(sort, view, "startedAt", extend=True)
    assert len(sort.sort_keys()) == 2


# --- REQ-029: ad-hoc column filters --------------------------------------------------


def test_funnel_fills_and_marks_the_view_modified() -> None:
    view = ViewSelection("needsFollowUp")
    filters = ColumnFilterSet(allowed=True)
    refusal = filters.apply(view, ColumnFilter("status", "distinctValues", ("open",)))
    assert refusal is None
    assert filters.funnel_filled("status")
    assert not filters.funnel_filled("mentorName")
    assert "adHocFilter" in view.state().modifications


def test_disallowed_filters_educate_instead_of_hiding() -> None:
    view = ViewSelection("needsFollowUp")
    filters = ColumnFilterSet(allowed=False)
    refusal = filters.apply(view, ColumnFilter("status", "textContains", ("open",)))
    assert refusal is not None
    assert "needsFollowUp" in refusal.why
    assert not filters.funnel_filled("status")
    assert not view.state().is_modified


def test_unknown_filter_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="regex"):
        ColumnFilter("status", "regex", ("a.*",))


def test_clearing_a_funnel_keeps_the_modified_flag() -> None:
    view = ViewSelection("v")
    filters = ColumnFilterSet(allowed=True)
    filters.apply(view, ColumnFilter("status", "distinctValues", ("open",)))
    filters.clear(view, "status")
    assert not filters.funnel_filled("status")
    assert view.state().is_modified


# --- REQ-021/022: actions ------------------------------------------------------------


def test_two_common_buttons_and_one_full_menu_with_help_last() -> None:
    menus = action_menus(ACTIONS, ("Edit", "Export"))
    assert [a.key for a in menus.buttons] == ["Edit", "Export"]
    # The full menu: common first, every action present, Help always last —
    # and it serves the dropdown AND the right-click menu (one list).
    assert [a.key for a in menus.menu] == ["Edit", "Export", "Remove", "Help"]
    assert menus.menu[-1] == HELP_ACTION


def test_unknown_common_action_is_a_configuration_bug() -> None:
    with pytest.raises(ValueError, match="Archive"):
        action_menus(ACTIONS, ("Edit", "Archive"))


def test_invalid_invocations_explain_instead_of_hiding() -> None:
    assert invalid_invocation(EDIT, 1) is None
    assert invalid_invocation(EXPORT, 0) is None
    two = invalid_invocation(EDIT, 2)
    assert two is not None and "2 rows" in two.why
    none = invalid_invocation(REMOVE, 0)
    assert none is not None and "none is selected" in none.why


def test_destructive_confirmation_counts_lists_and_stays_honest() -> None:
    titles = tuple(f"Engagement {i}" for i in range(1, 9))
    confirm = destructive_confirmation(REMOVE, titles, hidden_selected_count=3)
    assert confirm.title == "Remove 8 records?"
    assert confirm.listed_titles == titles[:5]
    assert confirm.more_count == 3
    assert confirm.hidden_rows_notice is not None
    assert "3 selected rows" in confirm.hidden_rows_notice
    assert confirm.honesty_note is not None
    assert "restore" in confirm.honesty_note


def test_non_destructive_confirmation_carries_no_permanence_claims() -> None:
    confirm = destructive_confirmation(EDIT, ("Engagement 1",))
    assert confirm.title == "Edit 1 record?"
    assert confirm.more_count == 0
    assert confirm.hidden_rows_notice is None
    assert confirm.honesty_note is None


# --- REQ-024: the keyboard model ------------------------------------------------------


def test_focus_starts_in_search_and_slash_returns_there() -> None:
    assert INITIAL_FOCUS == "searchBox"
    assert binding_for("/", "rows") == "focusSearchBox"


def test_enter_is_context_disambiguated() -> None:
    assert binding_for("Enter", "rows") == "openFocusedRecord"
    assert binding_for("Enter", "columnHeader") == "sortColumn"


def test_grid_wide_keys_apply_in_every_context() -> None:
    assert binding_for("Ctrl+A", "rows") == "selectEntireFilteredSet"
    assert binding_for("Shift+F10", "columnHeader") == "openActionsMenu"
    assert binding_for("F9", "rows") is None


def test_the_model_covers_full_no_mouse_operation() -> None:
    actions = {binding.action for binding in GRID_KEYBOARD_MODEL}
    assert actions == {
        "moveRowFocus",
        "toggleSelection",
        "extendSelection",
        "selectEntireFilteredSet",
        "openFocusedRecord",
        "openActionsMenu",
        "focusSearchBox",
        "sortColumn",
    }


# --- REQ-023/026: status bar ----------------------------------------------------------


def test_row_count_speaks_the_whole_filtered_set() -> None:
    assert row_count_label(200) == "200 rows"
    assert row_count_label(1) == "1 row"
    assert row_count_label(200, selected_count=10) == "200 rows, 10 Selected"


def test_hidden_selection_gets_the_keep_with_notice_variant() -> None:
    label = row_count_label(200, selected_count=10, hidden_selected_count=3)
    assert label == "200 rows, 10 Selected (3 not in current filter)"


def test_progress_bar_appears_past_a_few_seconds() -> None:
    quick = progress_display("Loading grid", 1.0)
    assert not quick.show_progress_bar and quick.estimate_seconds is None
    slow = progress_display("Recalculating Averages", 8.0)
    assert slow.show_progress_bar and slow.estimate_seconds == 8.0
    assert slow.message == "Recalculating Averages"


# --- REQ-031: restoration scopes -------------------------------------------------------


def test_only_the_view_choice_persists_long_term() -> None:
    scopes = {rule.piece: rule.scope for rule in STATE_RESTORATION}
    assert scopes.pop("activeView") == "longTerm"
    assert set(scopes.values()) == {"sessionOnly"}
    assert {"searchText", "scrollPosition", "selection", "focusedRow"} <= scopes.keys()
    assert RESTORED_DATA_REFRESHES is True
    assert last_view_preference_key("engagements") == "grid.engagements.lastView"


def test_last_view_preference_key_is_the_one_server_definition() -> None:
    # FND-018: ONE key definition — the panel shares api.grid_surface's, so
    # the UI and the deep-link fallback can never format the key differently.
    assert last_view_preference_key is grid_surface.last_view_preference_key


# --- REQ-030: the four grid states ------------------------------------------------------


def _inputs(**overrides: object) -> GridStateInputs:
    base: dict[str, object] = {
        "view_label": "Needs follow-up",
        "view_criteria": "engagements awaiting a mentor response",
        "filtered_count": 0,
        "unnarrowed_count": 0,
    }
    base.update(overrides)
    return GridStateInputs(**base)  # type: ignore[arg-type]


def test_rows_showing_means_no_state_notice() -> None:
    assert resolve_grid_state(_inputs(filtered_count=42)) is None


def test_permission_refusal_outranks_everything_and_names_the_grant() -> None:
    notice = resolve_grid_state(
        _inputs(permission_missing="engagementsForMentor", load_error="boom")
    )
    assert notice is not None and notice.kind == STATE_PERMISSION_REFUSAL
    assert "engagementsForMentor" in notice.message.why
    assert "administrator" in notice.message.what_next


def test_data_source_error_offers_retry_and_keeps_detail_aside() -> None:
    notice = resolve_grid_state(_inputs(load_error="timeout contacting warehouse"))
    assert notice is not None and notice.kind == STATE_DATA_SOURCE_ERROR
    assert notice.affordances == ("retry", "showDetail")
    assert notice.detail == "timeout contacting warehouse"
    assert "timeout" not in notice.message.why


def test_a_filter_never_masquerades_as_missing_data() -> None:
    notice = resolve_grid_state(_inputs(search_text="acme", unnarrowed_count=200))
    assert notice is not None and notice.kind == STATE_ZERO_FILTERED_SEARCH
    assert "'acme'" in notice.message.what_happened
    assert "200 rows" in notice.message.why
    assert notice.affordances == ("clearSearch", "clearFilters")


def test_column_filters_alone_still_read_as_filtered_to_zero() -> None:
    notice = resolve_grid_state(_inputs(ad_hoc_filter_count=1, unnarrowed_count=1))
    assert notice is not None and notice.kind == STATE_ZERO_FILTERED_SEARCH
    assert "column filters" in notice.message.what_happened


def test_unarmed_search_text_reads_as_a_truly_empty_view() -> None:
    # Two characters never ran a search (REQ-020), so zero rows is the
    # view's own emptiness, not a filter artifact.
    notice = resolve_grid_state(_inputs(search_text="ac"))
    assert notice is not None and notice.kind == STATE_EMPTY_VIEW
    assert "Needs follow-up" in notice.message.why
    assert "awaiting a mentor response" in notice.message.why
