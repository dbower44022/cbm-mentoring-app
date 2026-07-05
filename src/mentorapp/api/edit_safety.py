"""Concurrency, freshness & edit-safety process design (REQ-013, WTK-024).

No frontend shell exists yet (PI-002), so — like ``ui.record_preview`` — the
design is executable surface the shell renders verbatim. Four processes, one
answer each:

- **ConcurrentSaveConflictResolution** — what a client does with the write
  contract's 409-with-current-record (DB-S4/S12, ``StaleRowVersionError``).
  :func:`resolve_concurrent_save_conflict` is the single decision: edits
  disjoint from the other save auto-retry against the fresh ``rowVersion``
  (:class:`RetrySave`), values that already agree drop out entirely
  (:class:`AlreadyCurrent`), and a real overlap walks the user through every
  conflicted field with base/yours/theirs (:class:`ManualMerge`) — a
  conflicting save shows what changed and never silently overwrites.
- **CrossWindowFreshness** — for the same user, a save in any window updates
  every open window showing that record or a grid containing it. The
  broadcast payload (:class:`SaveNotice`) is EXACTLY a change-feed tuple
  (DB-S10), so the mechanism class is BroadcastChannel today and multi-user
  server push later is a transport upgrade, not a redesign. Multi-user
  liveness is designed-for but delivered later; manual refresh covers that
  gap (REQ-013 ruling). :func:`surface_needs_refresh` is the one fan-out
  rule, version-gated so replays and a window's own echo are no-ops.
- **DirtyWindowGuard** — closing a window with unsaved edits warns first.
  :meth:`EditorWindows.request_close` returns the guard
  (:class:`DirtyWindowGuard`) naming the dirty fields; the close proceeds
  only via :meth:`EditorWindows.discard_and_close` after the user confirms.
- **EditCollisionSwitch** — invoking Edit on a record already being edited
  offers switching to the existing editor (:class:`EditCollisionSwitch`)
  instead of opening a second one. Two editors over one record would let the
  same user race herself into the 409 path; the switch makes that
  impossible by construction.

The processes are welded: a :class:`SaveNotice` never refreshes a DIRTY
editor (that would destroy unsaved edits) — the stale base surfaces at save
time as the 409 the conflict resolver owns. Identity is
``(entityType, recordID)`` and records are wire-shaped dicts
(``serialize_record`` output, camelCase field names) — this module speaks
the API contract's vocabulary, never a UI one, which is why it lives in
``api`` and imports nothing from ``ui``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal, NamedTuple

from mentorapp.api.records import STRUCTURAL_FIELDS
from mentorapp.observability import get_logger

log = get_logger(__name__)

# After this many consecutive stale-save retries the user sees the merge
# walk-through even with no field overlap: something is hot-writing the
# record (a job, an import), and showing fresh values beats a client that
# silently spins against a moving target.
AUTO_RETRY_LIMIT: Final = 3

ROW_VERSION_FIELD: Final = "rowVersion"


# --- ConcurrentSaveConflictResolution (REQ-013, DB-S4) ----------------------------


@dataclass(frozen=True)
class FieldConflict:
    """One field both saves touched: the merge screen's base/yours/theirs row."""

    field_name: str
    base_value: Any
    your_value: Any
    their_value: Any


@dataclass(frozen=True)
class RetrySave:
    """Auto-retry: re-PATCH ``changes`` against the fresh ``row_version``.

    Issued only when every dirty field is untouched in the fresh copy — the
    DB-S4 auto-retry case. No user interaction; the retry is invisible.
    """

    changes: dict[str, Any]
    row_version: int


@dataclass(frozen=True)
class AlreadyCurrent:
    """The other save already carries every value the user typed.

    Nothing meaningful is left to send (retrying would be a no-op PATCH), so
    the editor simply rebases onto ``row_version`` and stays open.
    """

    row_version: int


@dataclass(frozen=True)
class ManualMerge:
    """The walk-through: the user resolves ``conflicts`` field by field.

    ``clean_changes`` are the dirty fields the other save did NOT touch —
    they ride along so the whole resolution lands in ONE follow-up PATCH
    against ``row_version``. Empty ``conflicts`` means the retry limit was
    exhausted (hot record), not an overlap.
    """

    conflicts: tuple[FieldConflict, ...]
    clean_changes: dict[str, Any]
    row_version: int


def resolve_concurrent_save_conflict(
    dirty_changes: dict[str, Any],
    loaded_record: dict[str, Any],
    current_record: dict[str, Any],
    *,
    attempt: int = 1,
) -> RetrySave | AlreadyCurrent | ManualMerge:
    """Decide what a stale save (409-with-current-record) does next.

    ``dirty_changes`` is the PATCH payload that was refused; ``loaded_record``
    is the copy the edit began from; ``current_record`` is the 409 body.
    Structural columns (DB-R2 exemption set) differ after ANY save and are
    never user-editable, so they never count as the other save's changes. A
    dirty field whose value now EQUALS the fresh copy is agreement, not
    conflict — it drops from the payload entirely. Pass the running 409
    count as ``attempt``; past :data:`AUTO_RETRY_LIMIT` the resolution is
    always the walk-through. Never returns anything that silently
    overwrites another save.
    """
    business_fields = (set(loaded_record) | set(current_record)) - STRUCTURAL_FIELDS
    their_changed = {
        name for name in business_fields if loaded_record.get(name) != current_record.get(name)
    }
    # Agreement pruning first: a value the other save already wrote is
    # neither a conflict nor worth re-sending.
    still_dirty = {
        name: value
        for name, value in dirty_changes.items()
        if value != current_record.get(name)
    }
    conflicted = sorted(set(still_dirty) & their_changed)
    current_version = int(current_record[ROW_VERSION_FIELD])

    if not still_dirty:
        return AlreadyCurrent(row_version=current_version)
    if not conflicted and attempt <= AUTO_RETRY_LIMIT:
        log.info(
            "stale save auto-retry",
            extra={"context": {"attempt": attempt, "fields": sorted(still_dirty)}},
        )
        return RetrySave(changes=still_dirty, row_version=current_version)
    log.info(
        "stale save manual merge",
        extra={"context": {"attempt": attempt, "conflictedFields": conflicted}},
    )
    return ManualMerge(
        conflicts=tuple(
            FieldConflict(
                field_name=name,
                base_value=loaded_record.get(name),
                your_value=still_dirty[name],
                their_value=current_record.get(name),
            )
            for name in conflicted
        ),
        clean_changes={n: v for n, v in still_dirty.items() if n not in set(conflicted)},
        row_version=current_version,
    )


# --- CrossWindowFreshness (REQ-013, DB-S10) ----------------------------------------


class SaveNotice(NamedTuple):
    """The same-user save broadcast: a change-feed tuple, nothing more.

    Deliberately the DB-S10 wire shape ``(entityType, recordID, rowVersion,
    changeKind)``: the BroadcastChannel payload today and the SSE/WebSocket
    payload later are the SAME tuple, so upgrading to multi-user push
    changes the transport, never the invalidation logic.
    """

    entity_type: str
    record_id: str
    row_version: int
    change_kind: str


def surface_needs_refresh(
    notice: SaveNotice,
    *,
    entity_type: str,
    record_id: str | None = None,
    row_version: int | None = None,
) -> bool:
    """The one fan-out rule: does an open surface refresh for ``notice``?

    A record surface (``record_id`` set) refreshes when it shows THAT record
    at an OLDER version — the version gate makes delivery idempotent and a
    window's echo of its own save a no-op. A grid surface (``record_id``
    None) refreshes for any change to its entity type: whether the saved
    record is in the visible slice is the grid cache's call
    (``recordID → rowVersion``, DB-S10), not the broadcast's.
    """
    if entity_type != notice.entity_type:
        return False
    if record_id is None:
        return True
    if record_id != notice.record_id:
        return False
    return row_version is None or row_version < notice.row_version


# --- DirtyWindowGuard + EditCollisionSwitch (REQ-013) -------------------------------


@dataclass(frozen=True)
class EditStarted:
    """This window now owns the record's ONLY editor, based at ``row_version``."""

    window_key: str
    entity_type: str
    record_id: str
    row_version: int


@dataclass(frozen=True)
class EditCollisionSwitch:
    """A second Edit on a record already being edited: offer the switch.

    The shell raises ``existing_window_key`` instead of opening a second
    editor — one user racing herself across her own windows is the one
    conflict the client can prevent outright rather than resolve after.
    """

    existing_window_key: str
    entity_type: str
    record_id: str


@dataclass(frozen=True)
class CloseAllowed:
    """No unsaved edits: the window closes with no ceremony."""

    window_key: str


@dataclass(frozen=True)
class DirtyWindowGuard:
    """Unsaved edits stand between the user and the close: warn, name them.

    The window stays open; the only ways forward are saving or an explicit
    :meth:`EditorWindows.discard_and_close` — a close can never eat edits
    silently.
    """

    window_key: str
    dirty_fields: tuple[str, ...]


class UnknownEditorError(Exception):
    """A window key with no open editor — a caller bug, never a user state."""


@dataclass
class _Editor:
    entity_type: str
    record_id: str
    row_version: int
    dirty_fields: set[str] = field(default_factory=set)

    def identity(self) -> tuple[str, str]:
        return (self.entity_type, self.record_id)


class EditorWindows:
    """Reference edit-safety behavior for one user session (the shell renders it).

    Owns the REQ-013 editor invariants: at most one editor per record
    (:class:`EditCollisionSwitch`), no close over unsaved edits without the
    guard (:class:`DirtyWindowGuard`), and a save fanning out as one
    :class:`SaveNotice`. Read surfaces (grids, previews, pop-outs) apply
    :func:`surface_needs_refresh` themselves; this controller answers only
    for editors, where freshness must never clobber typing.
    """

    def __init__(self) -> None:
        self._editors: dict[str, _Editor] = {}

    def begin_edit(
        self, window_key: str, entity_type: str, record_id: str, row_version: int
    ) -> EditStarted | EditCollisionSwitch:
        """Open an editor, or point at the one already editing this record.

        Matching is by record identity, never display title. Re-invoking
        Edit from the owning window itself is idempotent — the user is
        already where the switch would take them.
        """
        for key, editor in self._editors.items():
            if editor.identity() == (entity_type, record_id) and key != window_key:
                log.info(
                    "edit collision switch",
                    extra={"context": {"existingWindowKey": key, "recordId": record_id}},
                )
                return EditCollisionSwitch(key, entity_type, record_id)
        self._editors.setdefault(
            window_key, _Editor(entity_type, record_id, row_version)
        ).row_version = row_version
        return EditStarted(window_key, entity_type, record_id, row_version)

    def field_edited(self, window_key: str, field_name: str) -> None:
        """The user changed a field: it now stands behind the dirty guard."""
        self._editor(window_key).dirty_fields.add(field_name)

    def request_close(self, window_key: str) -> CloseAllowed | DirtyWindowGuard:
        """Close if clean; otherwise the guard — the window does NOT close."""
        editor = self._editor(window_key)
        if editor.dirty_fields:
            log.info(
                "dirty window guard",
                extra={
                    "context": {
                        "windowKey": window_key,
                        "dirtyFields": sorted(editor.dirty_fields),
                    }
                },
            )
            return DirtyWindowGuard(window_key, tuple(sorted(editor.dirty_fields)))
        del self._editors[window_key]
        return CloseAllowed(window_key)

    def discard_and_close(self, window_key: str) -> None:
        """The user confirmed the guard: edits are abandoned, window closes."""
        self._editor(window_key)
        del self._editors[window_key]
        log.info("dirty window discarded", extra={"context": {"windowKey": window_key}})

    def save_succeeded(self, window_key: str, new_row_version: int) -> SaveNotice:
        """A save landed: clear the dirty state, rebase, broadcast the notice.

        The editor stays open at the new version (saving is not closing);
        the returned :class:`SaveNotice` is what the shell puts on the
        BroadcastChannel — the single fan-out for every other window.
        """
        editor = self._editor(window_key)
        editor.dirty_fields.clear()
        editor.row_version = new_row_version
        return SaveNotice(editor.entity_type, editor.record_id, new_row_version, "updated")

    def notice_action(
        self, window_key: str, notice: SaveNotice
    ) -> Literal["refresh", "hold", "ignore"]:
        """What an EDITOR does with a save notice — the one place refresh yields.

        A clean editor on that record rebases (``refresh``; the shell reloads,
        then calls :meth:`rebased`). A DIRTY editor holds: auto-refreshing
        would destroy unsaved edits, so the stale base surfaces at save time
        as the 409 that :func:`resolve_concurrent_save_conflict` owns.
        Everything else — other records, own echo — is ``ignore``.
        """
        editor = self._editor(window_key)
        if not surface_needs_refresh(
            notice,
            entity_type=editor.entity_type,
            record_id=editor.record_id,
            row_version=editor.row_version,
        ):
            return "ignore"
        if editor.dirty_fields:
            log.info(
                "dirty editor holding through save notice",
                extra={"context": {"windowKey": window_key, "recordId": editor.record_id}},
            )
            return "hold"
        return "refresh"

    def rebased(self, window_key: str, row_version: int) -> None:
        """The shell reloaded the editor after ``refresh``: record the new base."""
        self._editor(window_key).row_version = row_version

    def open_editors(self) -> tuple[str, ...]:
        """Window keys of every open editor, in opening order."""
        return tuple(self._editors)

    def _editor(self, window_key: str) -> _Editor:
        if window_key not in self._editors:
            raise UnknownEditorError(window_key)
        return self._editors[window_key]
