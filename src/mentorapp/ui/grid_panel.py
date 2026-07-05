"""Grid panel UI design (WTK-043): regions, views, actions, keyboard, states.

The UI half of the universal grid (REQ-016, REQ-020..REQ-031), executable
surface the shell renders verbatim — the server half (search predicate,
aggregates, selection wire shape, export jobs, deep links) is WTK-042's
``api.grid_surface`` and is composed here, never re-implemented:

- **Three stacked regions, always** (REQ-016): :data:`GRID_FRAME` fixes the
  anatomy — action bar (view selector + edit button / search box / two
  common actions + Other Actions) over the data table over the status bar.
- **Search box** (REQ-020): :data:`GRID_SEARCH_BOX` declares the control;
  arming (3rd character) and the five-entry history reuse the
  ``grid_surface`` constants so the UI can never disagree with the server
  about when a search ran.
- **The view selector carries the modified flag**: :class:`ViewSelection`
  owns the lifecycle — header sorts, ad-hoc filters, and setting edits mark
  the active view modified (temporarily applied, saveable as a user view);
  selecting another view discards them; search never marks it (search is
  session state, REQ-031).
- **Actions: never hide, never disable** (REQ-021/022):
  :func:`action_menus` builds the two common-action buttons and the one
  full menu (common first, Help always last) that serves BOTH the Other
  Actions dropdown and the right-click menu; :func:`invalid_invocation`
  answers every wrong invocation with an educate explanation; destructive
  actions get :func:`destructive_confirmation` — exact count, first
  records + "and X more", the hidden-selected-rows addendum from
  ``grid_surface``, and soft-delete-honest wording.
- **Sorting** (REQ-025): :class:`SortModel` is the header behavior — click
  = sole sort (repeat toggles), Shift+click appends/toggles/removes — and
  :meth:`SortModel.badge_for` yields the arrow + 1-based position badge.
- **One keyboard model** (REQ-024): :data:`GRID_KEYBOARD_MODEL`, keyed by
  (key, context) so Enter can open a row AND sort a focused header without
  ambiguity; focus starts in the search box and ``/`` returns there.
- **Status bar** (REQ-023/026): :func:`row_count_label` renders the
  whole-filtered-set count with the keep-selection-with-notice variant;
  :func:`progress_display` shows a progress bar with estimate once work
  exceeds a few seconds.
- **Four distinguished grid states** (REQ-030): :func:`resolve_grid_state`
  is the one precedence decision (permission refusal > data-source error >
  zero rows, split filtered-to-zero vs truly-empty) so a filter can never
  masquerade as missing data and an error never renders as an empty grid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from mentorapp.api import grid_surface as _grid_surface
from mentorapp.api.grid_surface import (
    MIN_SEARCH_LENGTH,
    RECENT_SEARCH_LIMIT,
    hidden_rows_confirmation,
)
from mentorapp.observability import get_logger
from mentorapp.ui.auth_flows import EducateMessage
from mentorapp.ui.record_preview import PanelAction, single_row_required_message

log = get_logger(__name__)


# --- The three stacked regions (REQ-016) -------------------------------------------


@dataclass(frozen=True)
class GridFrame:
    """The universal grid anatomy the shell renders verbatim, top to bottom.

    Every list view is this frame — panels never rearrange the regions or
    omit one. The action-bar thirds are the standard's fixed placement:
    view machinery left, search middle, actions right.
    """

    regions: tuple[str, ...] = ("actionBar", "dataTable", "statusBar")
    action_bar_left: tuple[str, ...] = ("viewSelector", "editViewButton")
    action_bar_middle: tuple[str, ...] = ("searchBox",)
    action_bar_right: tuple[str, ...] = ("commonActionButtons", "otherActionsMenu")
    status_bar_middle: tuple[str, ...] = ("actionProgress",)
    status_bar_right: tuple[str, ...] = ("rowCount",)


GRID_FRAME = GridFrame()


# --- The search box (REQ-020) ------------------------------------------------------


@dataclass(frozen=True)
class SearchBox:
    """The action-bar search control.

    ``min_length`` / ``history_limit`` are the ``grid_surface`` values: the
    server decides when a search is real and what got remembered, the box
    only renders that contract. Search NARROWS the active view's results —
    it never replaces view filters — and scans displayed columns only.
    """

    min_length: int = MIN_SEARCH_LENGTH
    history_limit: int = RECENT_SEARCH_LIMIT
    initial_focus: bool = True
    refocus_key: str = "/"
    scope: str = "displayedColumns"
    narrows_view_filters: bool = True


GRID_SEARCH_BOX = SearchBox()


def search_is_live(search_text: str) -> bool:
    """Whether typed text runs a live search (REQ-020: from the 3rd character).

    Mirrors the server's remember rule: text that never armed never queries
    and never enters the recent-search history.
    """
    return len(search_text.strip()) >= MIN_SEARCH_LENGTH


# --- The view selector & the modified flag -----------------------------------------

# What counts as a temporary view modification. Search is deliberately absent:
# per the deep-link/restoration rulings it is session state, not view state.
VIEW_MODIFICATION_KINDS: Final[tuple[str, ...]] = (
    "sort",
    "adHocFilter",
    "columnWidth",
    "viewSettings",
)


@dataclass(frozen=True)
class ViewSelectorState:
    """What the selector renders: the active view and its modified indicator."""

    active_view_key: str
    modifications: tuple[str, ...]

    @property
    def is_modified(self) -> bool:
        return bool(self.modifications)


class ViewSelection:
    """The modified-flag lifecycle for one open grid.

    Temporary modifications apply until another view is selected (selection
    applies instantly and discards them) or the user saves them as a user
    view — then the new view IS the settings, so the flag clears and the
    selector lands on it.
    """

    def __init__(self, view_key: str) -> None:
        self._active = view_key
        self._modifications: list[str] = []

    def modify(self, kind: str) -> ViewSelectorState:
        """Record a temporary modification; unknown kinds are caller bugs."""
        if kind not in VIEW_MODIFICATION_KINDS:
            raise ValueError(f"unknown view modification kind: {kind!r}")
        if kind not in self._modifications:
            self._modifications.append(kind)
        return self.state()

    def select_view(self, view_key: str) -> ViewSelectorState:
        """Switch views instantly; unsaved temporary modifications are discarded."""
        self._active = view_key
        self._modifications.clear()
        return self.state()

    def save_as_user_view(self, view_key: str) -> ViewSelectorState:
        """The temporary settings become a user view; the flag clears."""
        log.info(
            "view saved as user view",
            extra={"context": {"fromView": self._active, "userView": view_key}},
        )
        self._active = view_key
        self._modifications.clear()
        return self.state()

    def state(self) -> ViewSelectorState:
        return ViewSelectorState(self._active, tuple(self._modifications))


# The ``userPreference`` key persisting a grid's last-displayed view: the ONE
# long-term piece of grid state (REQ-031); everything else in
# :data:`STATE_RESTORATION` is session-only. The definition lives in
# ``api.grid_surface`` (FND-018, DB-S13 — the last-used view is preference
# state, not a table) and is shared here, not duplicated, so the panel and
# the server's deep-link fallback can never format the key differently.
last_view_preference_key = _grid_surface.last_view_preference_key


# --- Sorting: header clicks, arrow + position badge (REQ-025) ----------------------


@dataclass(frozen=True)
class SortKey:
    field_name: str
    descending: bool = False


@dataclass(frozen=True)
class SortBadge:
    """What a sorted header shows: direction arrow + 1-based position number."""

    direction: str  # "asc" | "desc"
    position: int


class SortModel:
    """The multi-column header-sort behavior (REQ-025).

    Click = sole sort, repeat toggles direction. Shift+click appends a
    secondary/tertiary key; on an already-sorted column it toggles, and a
    third Shift+click removes the key. Callers mark the view modified via
    :func:`header_sort_click` — sorting is a temporary view modification.
    """

    def __init__(self, initial: tuple[SortKey, ...] = ()) -> None:
        self._keys: list[SortKey] = list(initial)

    def click(self, field_name: str) -> tuple[SortKey, ...]:
        sole = len(self._keys) == 1 and self._keys[0].field_name == field_name
        if sole:
            self._keys = [SortKey(field_name, not self._keys[0].descending)]
        else:
            self._keys = [SortKey(field_name)]
        return self.sort_keys()

    def shift_click(self, field_name: str) -> tuple[SortKey, ...]:
        for position, key in enumerate(self._keys):
            if key.field_name != field_name:
                continue
            if key.descending:
                # Third interaction on this key: descending → gone.
                del self._keys[position]
            else:
                self._keys[position] = SortKey(field_name, descending=True)
            return self.sort_keys()
        self._keys.append(SortKey(field_name))
        return self.sort_keys()

    def sort_keys(self) -> tuple[SortKey, ...]:
        return tuple(self._keys)

    def badge_for(self, field_name: str) -> SortBadge | None:
        for position, key in enumerate(self._keys, start=1):
            if key.field_name == field_name:
                return SortBadge("desc" if key.descending else "asc", position)
        return None


def header_sort_click(
    sort: SortModel, view: ViewSelection, field_name: str, *, extend: bool = False
) -> tuple[SortKey, ...]:
    """One header click: re-sort AND mark the view modified, atomically.

    This is the one place the sort→modified coupling lives, so no renderer
    can re-sort without the selector showing it.
    """
    keys = sort.shift_click(field_name) if extend else sort.click(field_name)
    view.modify("sort")
    return keys


# --- Ad-hoc column filters & the funnel (REQ-029) ----------------------------------

AD_HOC_FILTER_KINDS: Final[tuple[str, ...]] = (
    "distinctValues",
    "numericRange",
    "dateRange",
    "textContains",
)


@dataclass(frozen=True)
class ColumnFilter:
    """One header-funnel filter; operands travel serialized, the server does SQL.

    Distinct-value choices come from the server-side filtered set, and the
    predicate ANDs with view filters + search — both are ``grid_surface`` /
    ``list_engine`` behavior this shape merely addresses.
    """

    field_name: str
    kind: str
    operands: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in AD_HOC_FILTER_KINDS:
            raise ValueError(f"unknown ad-hoc filter kind: {self.kind!r}")


class ColumnFilterSet:
    """Per-grid funnel state; whether funnels exist at all is a view setting.

    A disallowed attempt returns an educate message instead of the control
    silently missing — the never-hide rule applied to filters.
    """

    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed
        self._filters: dict[str, ColumnFilter] = {}

    def apply(self, view: ViewSelection, column_filter: ColumnFilter) -> EducateMessage | None:
        if not self.allowed:
            return ad_hoc_filters_off_message(view.state().active_view_key)
        self._filters[column_filter.field_name] = column_filter
        view.modify("adHocFilter")
        return None

    def clear(self, view: ViewSelection, field_name: str) -> None:
        if self._filters.pop(field_name, None) is not None and not self._filters:
            # The view stays marked modified: clearing one funnel doesn't
            # un-modify sort or settings changes; only select/save resets.
            log.info(
                "last ad-hoc filter cleared",
                extra={"context": {"fieldName": field_name}},
            )

    def funnel_filled(self, field_name: str) -> bool:
        return field_name in self._filters

    def active(self) -> tuple[ColumnFilter, ...]:
        return tuple(self._filters.values())


def ad_hoc_filters_off_message(view_key: str) -> EducateMessage:
    return EducateMessage(
        what_happened="Column filters aren't on for this view.",
        why=f"The view '{view_key}' has ad-hoc column filters switched off "
        "in its settings — saved views are the primary way to filter.",
        what_next="Open the view settings to switch filters on (saveable as "
        "your own view), or pick a view that already filters this way.",
    )


# --- Actions: two buttons, one full menu, never hide (REQ-021/022) -----------------

# The per-action classification vocabulary the grid standard declares.
# Canonical home: workprocess registrations and panel actions both speak it.
ACTION_CLASSIFICATIONS: Final[tuple[str, ...]] = ("safe", "modifying", "destructive")

HELP_ACTION = PanelAction(
    key="Help", label="Help", selection_contract="none", classification="safe"
)

# How many affected records a destructive confirmation lists by name before
# collapsing to "and X more" — enough to recognize a mis-selection, few
# enough to read at a glance.
CONFIRMATION_LISTED_LIMIT: Final = 5

# Honest soft-delete wording (system-wide rule): never claim permanence.
SOFT_DELETE_HONESTY: Final = (
    "Records are removed from all views; an administrator can restore them."
)


@dataclass(frozen=True)
class ActionMenus:
    """The action-bar right side and the one full menu.

    ``menu`` serves BOTH the Other Actions dropdown and the grid's
    right-click menu — one list, so they can never diverge. Common actions
    lead, Help is always last.
    """

    buttons: tuple[PanelAction, ...]
    menu: tuple[PanelAction, ...]


def action_menus(actions: tuple[PanelAction, ...], common_keys: tuple[str, str]) -> ActionMenus:
    """Lay out a data set's actions: two common buttons + the full menu.

    Every action is always present — restriction happens via data-source
    permissions, never by trimming this list. Unknown common keys are a
    configuration bug, raised loudly.
    """
    by_key = {action.key: action for action in actions}
    missing = [key for key in common_keys if key not in by_key]
    if missing:
        raise ValueError(f"common action keys not in the action set: {missing}")
    buttons = tuple(by_key[key] for key in common_keys)
    rest = tuple(a for a in actions if a.key not in common_keys)
    return ActionMenus(buttons=buttons, menu=(*buttons, *rest, HELP_ACTION))


def invalid_invocation(action: PanelAction, selected_count: int) -> EducateMessage | None:
    """The never-hide explainer: why THIS invocation can't run, or ``None``.

    Selection contracts share ``record_preview``'s single-row explainer so
    the app has one voice for the same mistake.
    """
    if action.selection_contract == "single" and selected_count != 1:
        return single_row_required_message(action, selected_count)
    if action.selection_contract == "multiple" and selected_count == 0:
        return EducateMessage(
            what_happened=f"'{action.label}' didn't run.",
            why=f"'{action.label}' acts on the selected rows, and none is selected.",
            what_next="Select at least one row (Space, or Shift/Ctrl+click) "
            "and run the action again.",
        )
    return None


@dataclass(frozen=True)
class ActionConfirmation:
    """The one shared confirmation shape (REQ-022), rendered app-wide.

    Names the action and the EXACT count, lists the first records with an
    "and X more" tail, spells out selected-but-filtered-out rows, and stays
    honest about soft deletes.
    """

    title: str
    listed_titles: tuple[str, ...]
    more_count: int
    hidden_rows_notice: str | None
    honesty_note: str | None


def destructive_confirmation(
    action: PanelAction,
    record_titles: tuple[str, ...],
    *,
    hidden_selected_count: int = 0,
) -> ActionConfirmation:
    """Build the required confirmation for a destructive action.

    ``record_titles`` is the whole selection in grid order;
    ``hidden_selected_count`` is ``grid_surface.hidden_selection_count``'s
    answer for rows the current filter hides.
    """
    count = len(record_titles)
    items = "1 record" if count == 1 else f"{count} records"
    return ActionConfirmation(
        title=f"{action.label} {items}?",
        listed_titles=record_titles[:CONFIRMATION_LISTED_LIMIT],
        more_count=max(0, count - CONFIRMATION_LISTED_LIMIT),
        hidden_rows_notice=hidden_rows_confirmation(hidden_selected_count, action.label),
        honesty_note=SOFT_DELETE_HONESTY if action.classification == "destructive" else None,
    )


# --- The one keyboard model (REQ-024) ----------------------------------------------

INITIAL_FOCUS: Final = "searchBox"


@dataclass(frozen=True)
class KeyBinding:
    """One grid key: what it does and where it applies.

    ``context`` disambiguates keys that mean different things in different
    focus areas (Enter opens a row but sorts a focused column header).
    """

    keys: tuple[str, ...]
    action: str
    context: str  # "rows" | "columnHeader" | "grid"


GRID_KEYBOARD_MODEL: Final[tuple[KeyBinding, ...]] = (
    # Row focus auto-loads the next window at the bottom edge — arrows never
    # dead-end against the infinite scroll.
    KeyBinding(("ArrowUp", "ArrowDown"), "moveRowFocus", "rows"),
    KeyBinding(("Space",), "toggleSelection", "rows"),
    KeyBinding(("Shift+ArrowUp", "Shift+ArrowDown"), "extendSelection", "rows"),
    # Select-all means the ENTIRE filtered result set — never visible-first.
    KeyBinding(("Ctrl+A",), "selectEntireFilteredSet", "grid"),
    KeyBinding(("Enter",), "openFocusedRecord", "rows"),
    KeyBinding(("ContextMenu", "Shift+F10"), "openActionsMenu", "grid"),
    KeyBinding(("/",), "focusSearchBox", "grid"),
    KeyBinding(("Enter",), "sortColumn", "columnHeader"),
)


def binding_for(key: str, context: str) -> str | None:
    """Resolve one keypress; grid-wide bindings apply in every context."""
    for binding in GRID_KEYBOARD_MODEL:
        if key in binding.keys and binding.context in (context, "grid"):
            return binding.action
    return None


# --- Status bar: counts, notices, progress (REQ-023/026) ---------------------------


def row_count_label(
    total_rows: int, *, selected_count: int = 0, hidden_selected_count: int = 0
) -> str:
    """The status-bar right side over the WHOLE filtered set.

    ``total_rows`` is the server-side count (never the loaded window); the
    hidden variant is the keep-selection-with-notice rule made visible.
    """
    rows = "1 row" if total_rows == 1 else f"{total_rows} rows"
    if selected_count == 0:
        return rows
    label = f"{rows}, {selected_count} Selected"
    if hidden_selected_count > 0:
        label += f" ({hidden_selected_count} not in current filter)"
    return label


# When an action's expected duration crosses this, the status-bar message
# gains a progress bar with an estimate ("a few seconds", fixed here so
# every grid judges it identically). The 10-second background-task rule is
# the server's (grid_surface over_ten_seconds) — by then it's a job, not a bar.
PROGRESS_BAR_AFTER_SECONDS: Final = 3.0


@dataclass(frozen=True)
class StatusProgress:
    """The status-bar middle: a message, optionally a progress bar + estimate."""

    message: str
    show_progress_bar: bool
    estimate_seconds: float | None


def progress_display(message: str, expected_seconds: float) -> StatusProgress:
    slow = expected_seconds > PROGRESS_BAR_AFTER_SECONDS
    return StatusProgress(
        message=message,
        show_progress_bar=slow,
        estimate_seconds=expected_seconds if slow else None,
    )


# --- State restoration scopes (REQ-031) --------------------------------------------


@dataclass(frozen=True)
class RestorationRule:
    piece: str
    scope: str  # "longTerm" | "sessionOnly"


# Returning to a grid restores it EXACTLY while the data refreshes
# underneath; only the view choice survives past the session.
STATE_RESTORATION: Final[tuple[RestorationRule, ...]] = (
    RestorationRule("activeView", "longTerm"),
    RestorationRule("temporaryViewModifications", "sessionOnly"),
    RestorationRule("searchText", "sessionOnly"),
    RestorationRule("scrollPosition", "sessionOnly"),
    RestorationRule("selection", "sessionOnly"),
    RestorationRule("focusedRow", "sessionOnly"),
)
RESTORED_DATA_REFRESHES: Final = True


# --- The four distinguished grid states (REQ-030) ----------------------------------

STATE_EMPTY_VIEW: Final = "emptyView"
STATE_ZERO_FILTERED_SEARCH: Final = "zeroFilteredSearch"
STATE_DATA_SOURCE_ERROR: Final = "dataSourceError"
STATE_PERMISSION_REFUSAL: Final = "permissionRefusal"


@dataclass(frozen=True)
class GridStateNotice:
    """One rendered grid state: which one, the educate triple, its affordances.

    ``detail`` (data-source errors) is available on request, never dumped
    into the message.
    """

    kind: str
    message: EducateMessage
    affordances: tuple[str, ...] = ()
    detail: str | None = None


@dataclass(frozen=True)
class GridStateInputs:
    """Everything :func:`resolve_grid_state` needs, as plain values.

    ``filtered_count`` is the server-side count under search + ad-hoc
    filters; ``unnarrowed_count`` is the same view WITHOUT them — the gap is
    what "200 rows hidden" cites. ``permission_missing`` names the
    data-source permission the caller lacks.
    """

    view_label: str
    view_criteria: str
    filtered_count: int
    unnarrowed_count: int
    search_text: str = ""
    ad_hoc_filter_count: int = 0
    load_error: str | None = None
    permission_missing: str | None = None
    permission_grantor: str = "a system administrator"


def resolve_grid_state(inputs: GridStateInputs) -> GridStateNotice | None:
    """Decide which of the four states renders, or ``None`` (rows showing).

    Precedence: a refusal outranks an error (the query never ran), an error
    outranks emptiness (zero rows from a failed source is unknown, not
    empty), and narrowing splits filtered-to-zero from a truly empty view so
    a filter never masquerades as missing data.
    """
    if inputs.permission_missing is not None:
        return GridStateNotice(
            kind=STATE_PERMISSION_REFUSAL,
            message=EducateMessage(
                what_happened="This grid can't be shown.",
                why=f"Your account doesn't have the '{inputs.permission_missing}' "
                "data-source permission this view reads from.",
                what_next=f"Ask {inputs.permission_grantor} to grant it — "
                "access is per data source.",
            ),
        )
    if inputs.load_error is not None:
        return GridStateNotice(
            kind=STATE_DATA_SOURCE_ERROR,
            message=EducateMessage(
                what_happened="This grid couldn't load its data.",
                why="The data source didn't answer correctly.",
                what_next="Retry now — if it keeps failing, the technical "
                "detail helps an administrator find the cause.",
            ),
            affordances=("retry", "showDetail"),
            detail=inputs.load_error,
        )
    if inputs.filtered_count > 0:
        return None
    narrowed = search_is_live(inputs.search_text) or inputs.ad_hoc_filter_count > 0
    if narrowed:
        hidden = inputs.unnarrowed_count
        rows = "1 row is" if hidden == 1 else f"{hidden} rows are"
        needle = (
            f"'{inputs.search_text.strip()}'"
            if search_is_live(inputs.search_text)
            else "the current column filters"
        )
        return GridStateNotice(
            kind=STATE_ZERO_FILTERED_SEARCH,
            message=EducateMessage(
                what_happened=f"No rows match {needle}.",
                why=f"{rows} in this view but hidden by your search or "
                "column filters — the data is still there.",
                what_next="Clear the search or the column filters to see them.",
            ),
            affordances=("clearSearch", "clearFilters"),
        )
    return GridStateNotice(
        kind=STATE_EMPTY_VIEW,
        message=EducateMessage(
            what_happened="There's nothing in this view yet.",
            why=f"'{inputs.view_label}' shows {inputs.view_criteria}, "
            "and no records match right now.",
            what_next="Records appear here the moment they match — or switch "
            "views to see more of this data set.",
        ),
    )
