"""Per-field edit window & single-field write design (REQ-035, WTK-059).

No frontend shell exists yet (PI-002/PI-011), so — like ``edit_safety`` and
``form_validation`` — this is executable design surface the shell renders
verbatim. Double-clicking a read-only element opens a SMALL window editing
just that field, with its own Save and Cancel; Save commits that one field
immediately as a single-field PATCH under the standard concurrency check;
Cancel discards. No full-record save is ever required.

The design is deliberately thin because everything hard already has one
canonical home, and this window only composes it:

- **The write is the write contract, minimized.** :func:`single_field_patch`
  is a DB-S12 PATCH whose payload holds exactly one field plus ``rowVersion``
  — the smallest legal write. There is no per-field endpoint, verb, or
  validation path: the window's Save travels the same PATCH the full edit
  form sends, so the two edit paths cannot diverge.
- **Validation is the form engine's, on one field.** The window's input runs
  ``form_validation.validate_on_exit`` over the field's ``GET
  /schema/{entity}`` settings — the same ``validate_value`` the API runs at
  save (REQ-033). This module adds no validator.
- **A conflict is the standard conflict.** A 409 routes through
  :func:`~mentorapp.api.edit_safety.resolve_concurrent_save_conflict` with
  the window's one dirty field: untouched in the fresh copy → invisible
  auto-retry (the field-level sweet spot DB-S4 names explicitly); the other
  save already wrote the same value → nothing left to send, the window
  closes; a real same-field overlap → the standard base/yours/theirs
  walk-through, never a silent overwrite.
- **A save fans out as the standard notice.** :meth:`FieldEditors.save_succeeded`
  returns the same :class:`~mentorapp.api.edit_safety.SaveNotice` change-feed
  tuple every other save broadcasts — cross-window freshness has one path.

:class:`FieldEditors` is the reference behavior for one user session. At most
one field window per ``(entityType, recordID, fieldName)`` — a second
double-click on the same field switches to the existing window
(:class:`FieldEditSwitch`) instead of letting the user race herself on one
field. Different fields of the same record may be open at once, and the
window coexists with a full-record editor: single-field writes are disjoint
by construction, so overlap resolves through the standard 409 path rather
than being forbidden up front. Records are wire-shaped dicts
(``serialize_record`` output, camelCase) — this module speaks the API
contract's vocabulary, never a UI one, which is why it lives in ``api`` and
imports nothing from ``ui``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from mentorapp.api.edit_safety import (
    ROW_VERSION_FIELD,
    AlreadyCurrent,
    CloseAllowed,
    DirtyWindowGuard,
    ManualMerge,
    RetrySave,
    SaveNotice,
    UnknownEditorError,
    resolve_concurrent_save_conflict,
)
from mentorapp.api.records import STRUCTURAL_FIELDS
from mentorapp.observability import get_logger

log = get_logger(__name__)

# The one entry gesture (REQ-035): double-click on a field's read-only
# element, anywhere records render read-optimized (preview, pop-out, detail).
FIELD_EDIT_TRIGGER: Final = "doubleClick"


@dataclass(frozen=True)
class FieldEditWindow:
    """The small per-field window the shell renders verbatim (REQ-035).

    ``kind`` is declared so the shell can never inflate this into a full
    form: one field, its own Save and Cancel, nothing else. ``commits`` names
    the wire act Save performs — a single-field PATCH — so "no full-record
    save required" is a declared property, not an implementation accident.
    """

    kind: str = "smallWindow"
    trigger: str = FIELD_EDIT_TRIGGER
    save_label: str = "Save"
    cancel_label: str = "Cancel"
    commits: str = "singleFieldPatch"


FIELD_EDIT_WINDOW = FieldEditWindow()


def single_field_patch(field_name: str, value: Any, row_version: int) -> dict[str, Any]:
    """The smallest legal DB-S12 write: one field plus ``rowVersion``.

    This is the exact PATCH body the window's Save sends — the same verb,
    endpoint, and validation as any other edit, minimized to one field so an
    unrelated stale value can never fail this save.
    """
    return {field_name: value, ROW_VERSION_FIELD: row_version}


# --- Opening the window -------------------------------------------------------------


@dataclass(frozen=True)
class FieldEditOpened:
    """This window now owns the field's ONLY editor, based at ``row_version``."""

    window_key: str
    entity_type: str
    record_id: str
    field_name: str
    base_value: Any
    row_version: int


@dataclass(frozen=True)
class FieldEditSwitch:
    """A second double-click on a field already being edited: offer the switch.

    The shell raises ``existing_window_key`` instead of opening a second
    window — same-field self-racing is the one conflict this window can
    prevent outright rather than resolve after (the REQ-013 collision rule,
    narrowed to one field).
    """

    existing_window_key: str
    entity_type: str
    record_id: str
    field_name: str


@dataclass(frozen=True)
class FieldEditRefused:
    """The double-click landed on a field no one may edit: explain, don't open.

    ``reason`` is what the shell shows (PI-004's read-only-field-explanation
    surface renders it) — a refusal is never silent, and never a disabled
    gesture the user can't interrogate.
    """

    field_name: str
    reason: str


_STRUCTURAL_REASON = "This is a system field; it is maintained automatically."


# --- The reference behavior ----------------------------------------------------------


@dataclass
class _FieldEditor:
    entity_type: str
    record_id: str
    field_name: str
    loaded_record: dict[str, Any]
    value: Any
    dirty: bool = field(default=False)

    def identity(self) -> tuple[str, str, str]:
        return (self.entity_type, self.record_id, self.field_name)

    def base_value(self) -> Any:
        return self.loaded_record.get(self.field_name)

    def row_version(self) -> int:
        return int(self.loaded_record[ROW_VERSION_FIELD])


@dataclass(frozen=True)
class NothingToSave:
    """Save with the value back at base: the window closes, no write travels.

    The engine would no-op such a PATCH anyway (no version bump, no history)
    — but not sending it is the design: unchanged values never travel
    (DB-S12), even from this window.
    """

    window_key: str


@dataclass(frozen=True)
class CommitSingleField:
    """Save has a real change: the shell PATCHes ``payload`` for the record.

    ``payload`` is :func:`single_field_patch` output — exactly one field plus
    ``rowVersion``. The window stays open until :meth:`FieldEditors.save_succeeded`
    or :meth:`FieldEditors.save_conflicted` reports how the write landed.
    """

    window_key: str
    entity_type: str
    record_id: str
    payload: dict[str, Any]


class FieldEditors:
    """Reference per-field edit behavior for one user session (REQ-035).

    Owns the window lifecycle over the standard write machinery: open on
    double-click (or switch/refuse), commit exactly one field under
    ``rowVersion``, route a 409 through the standard resolver, close on
    Cancel with no ceremony, and guard only the NON-Cancel close paths —
    Cancel IS the discard, but a window X over an edited value still warns
    (the REQ-013 dirty guard, one field wide).
    """

    def __init__(self) -> None:
        self._editors: dict[str, _FieldEditor] = {}

    def open(
        self,
        window_key: str,
        entity_type: str,
        field_name: str,
        loaded_record: dict[str, Any],
        *,
        read_only_reason: str | None = None,
    ) -> FieldEditOpened | FieldEditSwitch | FieldEditRefused:
        """Open the field's window from the read surface's own record copy.

        ``loaded_record`` is the wire-shaped record the double-clicked element
        rendered from — the WHOLE record, not just the field, because the 409
        resolver needs the full base copy to tell "they changed my field"
        from "they changed something else". Structural columns are refused
        (the write engine rejects them as ``readOnlyField``); a caller-known
        read-only field passes its explanation as ``read_only_reason``.
        Re-invoking from the owning window itself is idempotent.
        """
        record_id = str(loaded_record[f"{entity_type}ID"])
        if field_name in STRUCTURAL_FIELDS or field_name == f"{entity_type}ID":
            return FieldEditRefused(field_name, _STRUCTURAL_REASON)
        if read_only_reason is not None:
            return FieldEditRefused(field_name, read_only_reason)
        for key, editor in self._editors.items():
            if editor.identity() == (entity_type, record_id, field_name) and key != window_key:
                log.info(
                    "field edit switch",
                    extra={"context": {"existingWindowKey": key, "fieldName": field_name}},
                )
                return FieldEditSwitch(key, entity_type, record_id, field_name)
        editor = _FieldEditor(
            entity_type=entity_type,
            record_id=record_id,
            field_name=field_name,
            loaded_record=dict(loaded_record),
            value=loaded_record.get(field_name),
        )
        self._editors[window_key] = editor
        log.info(
            "field edit opened",
            extra={
                "context": {
                    "windowKey": window_key,
                    "entityType": entity_type,
                    "recordId": record_id,
                    "fieldName": field_name,
                }
            },
        )
        return FieldEditOpened(
            window_key,
            entity_type,
            record_id,
            field_name,
            editor.base_value(),
            editor.row_version(),
        )

    def edit_value(self, window_key: str, value: Any) -> None:
        """The user changed the input; dirtiness is value-vs-base, not keystrokes."""
        editor = self._editor(window_key)
        editor.value = value
        editor.dirty = value != editor.base_value()

    def request_save(self, window_key: str) -> NothingToSave | CommitSingleField:
        """Save: emit the single-field PATCH, or close when nothing changed."""
        editor = self._editor(window_key)
        if not editor.dirty:
            del self._editors[window_key]
            return NothingToSave(window_key)
        return CommitSingleField(
            window_key,
            editor.entity_type,
            editor.record_id,
            single_field_patch(editor.field_name, editor.value, editor.row_version()),
        )

    def save_succeeded(self, window_key: str, new_row_version: int) -> SaveNotice:
        """The PATCH landed: close the window, broadcast the standard notice.

        Committing is the window's whole job (REQ-035: Save commits that
        single field immediately), so unlike the full editor it does not stay
        open. The returned tuple is the same fan-out every save uses.
        """
        editor = self._editor(window_key)
        del self._editors[window_key]
        log.info(
            "field edit committed",
            extra={
                "context": {
                    "windowKey": window_key,
                    "fieldName": editor.field_name,
                    "recordId": editor.record_id,
                }
            },
        )
        return SaveNotice(editor.entity_type, editor.record_id, new_row_version, "updated")

    def save_conflicted(
        self, window_key: str, current_record: dict[str, Any], *, attempt: int = 1
    ) -> RetrySave | AlreadyCurrent | ManualMerge:
        """Route the 409 body through the standard resolver, one field dirty.

        :class:`RetrySave` — the other save left this field alone; the shell
        re-PATCHes invisibly against the fresh version (the field-level
        auto-retry DB-S4 names). :class:`AlreadyCurrent` — the other save
        already wrote this very value; the window closes, nothing travels.
        :class:`ManualMerge` — a real same-field overlap; the window shows
        the standard base/yours/theirs walk-through. The 409 body becomes the
        window's new base either way, so repeated conflicts always resolve
        against the newest copy. Pass the running 409 count as ``attempt``.
        """
        editor = self._editor(window_key)
        resolution = resolve_concurrent_save_conflict(
            {editor.field_name: editor.value},
            editor.loaded_record,
            current_record,
            attempt=attempt,
        )
        if isinstance(resolution, AlreadyCurrent):
            del self._editors[window_key]
        else:
            editor.loaded_record = dict(current_record)
            editor.dirty = editor.value != editor.base_value()
        return resolution

    def cancel(self, window_key: str) -> None:
        """Cancel discards, full stop (REQ-035) — it IS the explicit discard,
        so it never re-asks; an unknown key is a caller bug."""
        editor = self._editor(window_key)
        del self._editors[window_key]
        log.info(
            "field edit cancelled",
            extra={"context": {"windowKey": window_key, "fieldName": editor.field_name}},
        )

    def request_close(self, window_key: str) -> CloseAllowed | DirtyWindowGuard:
        """A NON-Cancel close (window X, shell shutdown): guard edited values.

        Cancel carries the user's intent to discard; a bare close does not,
        so an edited value raises the standard :class:`DirtyWindowGuard` and
        the window stays open until saved or explicitly discarded.
        """
        editor = self._editor(window_key)
        if editor.dirty:
            log.info(
                "field edit dirty guard",
                extra={"context": {"windowKey": window_key, "fieldName": editor.field_name}},
            )
            return DirtyWindowGuard(window_key, (editor.field_name,))
        del self._editors[window_key]
        return CloseAllowed(window_key)

    def open_editors(self) -> tuple[str, ...]:
        """Window keys of every open field window, in opening order."""
        return tuple(self._editors)

    def _editor(self, window_key: str) -> _FieldEditor:
        if window_key not in self._editors:
            raise UnknownEditorError(window_key)
        return self._editors[window_key]
