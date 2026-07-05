"""Record preview & pop-out window design: docked preview, pop-outs (WTK-021).

The UI-layer design for REQ-012 (multi-window record viewing). No frontend
shell exists yet (PI-002), so — like ``home_panel`` — the design is
executable surface the shell renders verbatim:

- **The preview is a read mode, never an edit form.** :data:`RECORD_PREVIEW`
  fixes the docked pane: read-optimized, zero edit controls, docked when
  window size allows. Editing is reachable only by the two standard paths
  (the Edit action, or double-click on a read-only element for the per-field
  edit window) — the pane declares those paths; it never hosts them.
- **The docked preview follows the focused row, live.** :class:`RecordWindows`
  is the reference behavior: every focus change re-targets the docked
  preview immediately; an empty grid or cleared focus shows
  :data:`NO_ROW_FOCUSED` (educate voice), never a blank pane.
- **Pop-outs are real browser windows pinned to their record.** Popping out
  never re-targets with the selection; several pop-outs work at once across
  monitors; the docked preview keeps following the grid underneath them.
  Popping out a record that is already open raises the existing window with
  a notice instead of stacking a duplicate — the layout standard's
  switch-to-that-window rule, applied to read windows. Pop-outs survive
  main-window close (they are independent browser windows), and get the
  standard header minus navigation (record windows, not panel hosts).
- **Both entry points are declared actions.** :data:`OPEN_RECORD_PREVIEW` and
  :data:`POP_OUT_RECORD` carry the grid standard's declaration (selection
  contract ``single``, classification ``safe``) so the never-hide machinery
  can explain an invalid invocation (:func:`single_row_required_message`)
  instead of hiding or graying the action.
- **Same-user cross-window sync has one fan-out answer.**
  :meth:`RecordWindows.record_saved` names exactly which open surfaces show
  the saved record — the BroadcastChannel-class refresh the layout standard
  requires rides this, so no window ever shows a stale copy after a save in
  another window.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mentorapp.observability import get_logger
from mentorapp.storage import SELECTION_CONTRACTS
from mentorapp.ui.auth_flows import EducateMessage
from mentorapp.ui.home_panel import HOME_FRAME

log = get_logger(__name__)

# The standard's implementer's choice (right vs bottom), exercised here: the
# app targets wide desktop screens, so a right dock spends surplus horizontal
# room and keeps the grid's visible row count intact.
PREVIEW_DOCK_POSITION = "right"

# Pop-outs keep the standard header's right side but drop panel navigation —
# they are record windows, not panel hosts (layout standard). Reused from the
# home frame so the header has one canonical definition.
POP_OUT_HEADER_RIGHT = HOME_FRAME.header_right
POP_OUT_HAS_NAVIGATION = False


@dataclass(frozen=True)
class RecordRef:
    """One record as the grid hands it to a window: identity plus a title.

    Identity is ``(entity_type, record_id)`` — see :meth:`identity`. The
    title is display-only and may go stale between windows; it never
    participates in pinning or sync matching.
    """

    entity_type: str
    record_id: str
    title: str

    def identity(self) -> tuple[str, str]:
        return (self.entity_type, self.record_id)


# --- The docked preview pane (REQ-012) -------------------------------------------


@dataclass(frozen=True)
class PreviewPane:
    """The docked preview the shell renders verbatim.

    ``edit_controls`` is a declared impossibility, not a default: previews
    are read-optimized and editing is an explicit act. ``edit_paths`` are
    the only two ways out of read mode (full edit screen via the Edit
    action; per-field edit window via double-click on a read-only element).
    """

    dock_position: str = PREVIEW_DOCK_POSITION
    docked_when: str = "windowSizeAllows"
    read_optimized: bool = True
    edit_controls: bool = False
    edit_paths: tuple[str, ...] = ("editAction", "perFieldDoubleClick")


RECORD_PREVIEW = PreviewPane()

NO_ROW_FOCUSED = EducateMessage(
    what_happened="Nothing is previewed yet.",
    why="No row is focused in the grid — the preview always shows the focused row.",
    what_next="Click a row, or move to one with the arrow keys, to preview it here.",
)


@dataclass(frozen=True)
class PreviewContent:
    """What the docked pane shows; ``notice`` is set only when no row is focused."""

    record: RecordRef | None
    notice: EducateMessage | None = None


# --- The two declared actions (grid standard: never hide, always explain) --------


@dataclass(frozen=True)
class PanelAction:
    """A grid action declaration (grid standard vocabulary, storage-validated).

    ``selection_contract`` must be one of :data:`~mentorapp.storage.SELECTION_CONTRACTS`
    — the same vocabulary workprocess registrations use, so the one
    invalid-invocation explainer serves both.
    """

    key: str
    label: str
    selection_contract: str
    classification: str

    def __post_init__(self) -> None:
        if self.selection_contract not in SELECTION_CONTRACTS:
            raise ValueError(f"unknown selection contract: {self.selection_contract!r}")


# Both are safe (read-only) and act on exactly one row: a preview or pop-out
# of "several records" has no meaning — the explainer says so instead of the
# action ever hiding or graying out.
OPEN_RECORD_PREVIEW = PanelAction(
    key="OpenRecordPreview",
    label="Preview",
    selection_contract="single",
    classification="safe",
)
POP_OUT_RECORD = PanelAction(
    key="PopOutRecord",
    label="Pop out",
    selection_contract="single",
    classification="safe",
)


def single_row_required_message(action: PanelAction, selected_count: int) -> EducateMessage:
    """The never-hide explainer for a ``single``-contract action invoked wrong.

    Names the action and the actual selection so the user learns the
    contract, not just that "it didn't work".
    """
    if selected_count == 0:
        why = f"'{action.label}' needs exactly one selected row, and none is selected."
        what_next = "Select the row you want and run the action again."
    else:
        why = (
            f"'{action.label}' works on exactly one record, "
            f"but {selected_count} rows are selected."
        )
        what_next = "Narrow the selection to a single row and run the action again."
    return EducateMessage(
        what_happened=f"'{action.label}' didn't run.",
        why=why,
        what_next=what_next,
    )


# --- Pop-out windows & cross-window sync (REQ-012) --------------------------------


@dataclass(frozen=True)
class PopOutWindow:
    """One open pop-out: a REAL browser window pinned to its record.

    ``kind`` is declared so the shell can never downgrade pop-outs to modal
    or in-page overlays — real windows are what makes multi-monitor work.
    """

    window_key: str
    record: RecordRef
    kind: str = "browserWindow"


@dataclass(frozen=True)
class PopOutResult:
    """Outcome of a pop-out request; ``notice`` set = raised an existing window."""

    window: PopOutWindow
    notice: EducateMessage | None = None


@dataclass(frozen=True)
class SyncFanout:
    """Who must re-render after a save: the docked pane and/or pop-out windows."""

    docked: bool
    pop_out_keys: tuple[str, ...]


class UnknownWindowError(Exception):
    """A window key the controller has never opened — a caller bug."""


def already_open_message(record: RecordRef) -> EducateMessage:
    """Educate-voice notice when a pop-out request lands on an existing window."""
    return EducateMessage(
        what_happened=f"'{record.title}' is already open in its own window.",
        why="Each record gets one pop-out window; a second copy would just cover it.",
        what_next="That window has been brought to the front — it's live and up to date.",
    )


@dataclass
class _Windows:
    focused: RecordRef | None = None
    pop_outs: dict[str, PopOutWindow] = field(default_factory=dict)
    opened_count: int = 0


class RecordWindows:
    """Reference multi-window behavior for one user session (the shell renders it).

    Owns the three REQ-012 invariants: the docked preview follows the focused
    row and ONLY the focused row (pop-outs never re-target), any number of
    pop-outs coexist pinned each to its record, and a save fans out to every
    open surface showing that record — the same-user cross-window sync the
    layout standard makes standard for v1.
    """

    def __init__(self) -> None:
        self._state = _Windows()

    # --- docked preview ---

    def focus_row(self, record: RecordRef | None) -> PreviewContent:
        """Re-target the docked preview to the grid's focused row, immediately.

        ``None`` (empty grid, cleared focus) previews nothing WITH
        :data:`NO_ROW_FOCUSED` — a blank pane would let "no focus" masquerade
        as "no data". Pop-outs are untouched: they are pinned.
        """
        self._state.focused = record
        return self.preview()

    def preview(self) -> PreviewContent:
        """What the docked pane currently shows."""
        if self._state.focused is None:
            return PreviewContent(record=None, notice=NO_ROW_FOCUSED)
        return PreviewContent(record=self._state.focused)

    # --- pop-outs ---

    def pop_out(self, record: RecordRef) -> PopOutResult:
        """Open a pop-out pinned to ``record``, or raise the one already showing it.

        Matching is by record identity, not title — a stale title in an old
        grid row must still find the live window. The raise-existing path
        carries :func:`already_open_message`; silently focusing a window the
        user didn't see appear would read as the action doing nothing.
        """
        for window in self._state.pop_outs.values():
            if window.record.identity() == record.identity():
                log.info(
                    "pop-out raised existing window",
                    extra={"context": {"windowKey": window.window_key}},
                )
                return PopOutResult(window, notice=already_open_message(window.record))
        self._state.opened_count += 1
        window = PopOutWindow(f"popout-{self._state.opened_count}", record)
        self._state.pop_outs[window.window_key] = window
        log.info(
            "pop-out opened",
            extra={
                "context": {
                    "windowKey": window.window_key,
                    "entityType": record.entity_type,
                    "recordId": record.record_id,
                }
            },
        )
        return PopOutResult(window)

    def open_pop_outs(self) -> tuple[PopOutWindow, ...]:
        """Every open pop-out, in opening order."""
        return tuple(self._state.pop_outs.values())

    def close_pop_out(self, window_key: str) -> None:
        """The user closed a pop-out window; an unknown key is a caller bug."""
        if window_key not in self._state.pop_outs:
            raise UnknownWindowError(window_key)
        del self._state.pop_outs[window_key]
        log.info("pop-out closed", extra={"context": {"windowKey": window_key}})

    def main_window_closed(self) -> tuple[PopOutWindow, ...]:
        """Pop-outs SURVIVE main-window close (layout standard): the docked
        preview dies with its window; the independent browser windows stay."""
        self._state.focused = None
        return self.open_pop_outs()

    # --- same-user cross-window sync ---

    def record_saved(self, record: RecordRef) -> SyncFanout:
        """Name every open surface showing the just-saved record.

        The shell broadcasts a save (BroadcastChannel-class) and each window
        re-renders itself; this is the single answer to "who is affected",
        so no surface is ever missed or refreshed twice.
        """
        focused = self._state.focused
        return SyncFanout(
            docked=focused is not None and focused.identity() == record.identity(),
            pop_out_keys=tuple(
                w.window_key
                for w in self._state.pop_outs.values()
                if w.record.identity() == record.identity()
            ),
        )
