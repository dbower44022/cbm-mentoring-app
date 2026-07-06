"""Full-screen edit form & lifecycle design (REQ-032, WTK-066).

No frontend shell exists yet (PI-002/PI-011), so — like ``readonly_fields``
and ``api.edit_safety`` — this is executable design surface the shell renders
verbatim. The Edit action opens ONE full-screen form; this module fixes its
frame and owns its value lifecycle: original values, dirty tracking, the
leave-with-unsaved-changes warning, Cancel's revert-to-original, and the
minimal PATCH a Save commits.

The frame (:data:`EDIT_FORM_SCREEN`): full screen, field positions roughly
matching the read view scaled up for edit controls, a large Save button
(Ctrl+S — Save's shortcut everywhere a Save exists), Cancel reverting to the
original values, Esc requesting leave (the guard runs first), and initial
focus on the first editable field.

What this module deliberately does NOT own (one canonical home each):

- **Which fields are editable** — :func:`~mentorapp.ui.readonly_fields.edit_form_disposition`
  decides; :meth:`EditForm.from_disposition` consumes its output verbatim, so
  the form can never grow an editor the disposition denied.
- **Validation** — ``api.form_validation`` (per-field on exit, the save
  sweep). The shell runs ``sweep_before_save`` on :class:`CommitChanges`'s
  payload before the PATCH; this module never re-checks validity.
- **Window-level safety** — ``api.edit_safety``: edit collision, the
  window-CLOSE guard, save fan-out, and the 409 path. The shell registers the
  editor via ``EditorWindows.begin_edit`` and mirrors each first change with
  ``field_edited``. ``EditorWindows`` has no un-dirty API, so a field typed
  back to its original stays behind the window-close guard until save or
  discard — the close warning erring toward warning is safe; this module's
  value-level :meth:`EditForm.dirty_fields` stays the truth for the PATCH
  payload and the leave guard's field list.

Dirty is a VALUE comparison, never a touched flag: a field retyped back to
its original drops out, matching the conflict resolver's agreement pruning —
so Cancel, the leave guard, and the PATCH payload all answer from the same
comparison and a no-change Save is :class:`NothingToSave`, never a no-op
PATCH. Records are wire-shaped dicts (``serialize_record`` output, camelCase
field names) and the commit vocabulary mirrors ``api.field_edit``'s
(:class:`CommitChanges` beside ``CommitSingleField``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from mentorapp.api.edit_safety import ROW_VERSION_FIELD
from mentorapp.observability import get_logger
from mentorapp.ui.auth_flows import EducateMessage
from mentorapp.ui.readonly_fields import EditableField, ReadOnlyField
from mentorapp.ui.record_preview import PanelAction

log = get_logger(__name__)

# Ctrl+S = Save everywhere a Save exists (forms standard keyboard rule).
SAVE_SHORTCUT: Final = "Ctrl+S"


@dataclass(frozen=True)
class SaveControl:
    """The commit control REQ-032 names: large, never subtle.

    ``prominence`` is declared so the shell can't render Save as one more
    toolbar button — a full-screen form's exit-with-my-work path is the
    biggest thing on it.
    """

    label: str = "Save"
    prominence: str = "large"
    shortcut: str = SAVE_SHORTCUT


@dataclass(frozen=True)
class CancelControl:
    """Cancel reverts to the original values at any time before Save.

    ``behavior`` is the contract: revert, not close — the form stays open on
    the restored values (leaving is a navigation act the guard owns).
    """

    label: str = "Cancel"
    behavior: str = "revertToOriginal"


@dataclass(frozen=True)
class EditFormScreen:
    """The full-screen edit frame the shell renders verbatim (REQ-032).

    ``field_positions`` pins the layout rule — fields sit roughly where the
    read view puts them, ``control_scale`` scaled up because edit controls
    need more room. ``escape`` requests leave (the dirty guard runs first);
    ``initial_focus`` is the forms standard's first-editable-field rule
    (:func:`first_editable_field` resolves it over a disposition).
    """

    presentation: str = "fullScreen"
    field_positions: str = "matchesReadView"
    control_scale: str = "scaledUpForEditControls"
    initial_focus: str = "firstEditableField"
    escape: str = "requestLeave"
    save: SaveControl = SaveControl()
    cancel: CancelControl = CancelControl()


EDIT_FORM_SCREEN: Final = EditFormScreen()

# The grid/preview action that opens this form: modifying (it exists to change
# the record), acting on exactly one row — the never-hide explainer for a bad
# selection is record_preview.single_row_required_message, same as Preview's.
EDIT_RECORD = PanelAction(
    key="EditRecord",
    label="Edit",
    selection_contract="single",
    classification="modifying",
)


def first_editable_field(
    dispositions: Sequence[EditableField | ReadOnlyField],
) -> EditableField | None:
    """Where focus starts: the first editable field in display order.

    ``None`` means the whole form rendered read-only (every field computed,
    system, or permission-blocked) — focus falls to the Save row and there is
    nothing to guard, revert, or commit.
    """
    for disposition in dispositions:
        if isinstance(disposition, EditableField):
            return disposition
    return None


def leave_warning(dirty_labels: Sequence[str]) -> EducateMessage:
    """The unsaved-changes warning, naming every changed field (educate voice)."""
    count = len(dirty_labels)
    fields = "field has" if count == 1 else "fields have"
    return EducateMessage(
        what_happened="This page has unsaved changes.",
        why=f"{count} {fields} been changed but not saved: {', '.join(dirty_labels)}.",
        what_next="Save to keep the changes, or discard them to leave without saving.",
    )


@dataclass(frozen=True)
class LeaveAllowed:
    """No unsaved edits: navigation proceeds with no ceremony."""


@dataclass(frozen=True)
class DirtyLeaveGuard:
    """Unsaved edits stand between the user and the navigation: warn, name them.

    The form stays put; the only ways forward are saving or an explicit
    :meth:`EditForm.discard` after the user confirms — leaving can never eat
    edits silently.
    """

    dirty_fields: tuple[str, ...]
    warning: EducateMessage


@dataclass(frozen=True)
class NothingToSave:
    """Every field matches its original: Save is a declared no-op, no PATCH."""


@dataclass(frozen=True)
class CommitChanges:
    """The Save: PATCH exactly the changed fields against ``row_version``.

    The shell runs the ``form_validation`` save sweep on ``changes`` first,
    then ``partial_update``; a 409 lands in ``edit_safety``'s resolver. The
    form stays open until :meth:`EditForm.saved` rebases it.
    """

    changes: dict[str, Any]
    row_version: int


class NotEditableError(Exception):
    """An edit against a field the disposition denied — a shell bug, never a user state."""


class EditForm:
    """One open full-screen edit form's value lifecycle (REQ-032).

    Built over the disposition and the loaded wire-shaped record; owns the
    original values and answers dirty, Cancel, leave, and Save from the one
    value comparison. Field order everywhere (dirty lists, the warning) is
    the disposition's display order.
    """

    def __init__(
        self, entity_type: str, editable: Sequence[EditableField], record: Mapping[str, Any]
    ) -> None:
        self._entity_type = entity_type
        self._editable = {f.field_name: f for f in editable}
        self._original: dict[str, Any] = {name: record.get(name) for name in self._editable}
        self._current = dict(self._original)
        self._row_version = int(record[ROW_VERSION_FIELD])

    @classmethod
    def from_disposition(
        cls,
        entity_type: str,
        dispositions: Sequence[EditableField | ReadOnlyField],
        record: Mapping[str, Any],
    ) -> EditForm:
        """The standard construction: editors exactly where the disposition allows."""
        editable = [d for d in dispositions if isinstance(d, EditableField)]
        return cls(entity_type, editable, record)

    @property
    def row_version(self) -> int:
        """The optimistic-concurrency base the next Save commits against."""
        return self._row_version

    def edit(self, field_name: str, value: Any) -> None:
        """The user changed a field; a value equal to the original is clean again."""
        if field_name not in self._editable:
            raise NotEditableError(field_name)
        self._current[field_name] = value

    def value_of(self, field_name: str) -> Any:
        """What the control shows now (the original until edited)."""
        if field_name not in self._editable:
            raise NotEditableError(field_name)
        return self._current[field_name]

    def dirty_fields(self) -> tuple[str, ...]:
        """Fields whose value differs from the original, in display order."""
        return tuple(
            name for name in self._editable if self._current[name] != self._original[name]
        )

    def is_dirty(self) -> bool:
        return bool(self.dirty_fields())

    def cancel(self) -> dict[str, Any]:
        """Revert every field to its original value; the form stays open.

        Returns the restored values by field name (what the shell re-renders);
        empty means nothing was dirty and nothing changed on screen.
        """
        reverted = {name: self._original[name] for name in self.dirty_fields()}
        self._current = dict(self._original)
        if reverted:
            log.info(
                "edit form cancel revert",
                extra={
                    "context": {
                        "entityType": self._entity_type,
                        "revertedFields": sorted(reverted),
                    }
                },
            )
        return reverted

    def request_leave(self) -> LeaveAllowed | DirtyLeaveGuard:
        """Leave if clean; otherwise the warning — navigation does NOT proceed."""
        dirty = self.dirty_fields()
        if not dirty:
            return LeaveAllowed()
        log.info(
            "edit form leave guard",
            extra={"context": {"entityType": self._entity_type, "dirtyFields": sorted(dirty)}},
        )
        labels = [self._editable[name].field_label for name in dirty]
        return DirtyLeaveGuard(dirty_fields=dirty, warning=leave_warning(labels))

    def discard(self) -> None:
        """The user confirmed the guard: edits are abandoned, navigation may proceed."""
        self._current = dict(self._original)
        log.info("edit form discarded", extra={"context": {"entityType": self._entity_type}})

    def request_save(self) -> NothingToSave | CommitChanges:
        """What Save does: the minimal PATCH, or a declared no-op when clean."""
        changes = {name: self._current[name] for name in self.dirty_fields()}
        if not changes:
            return NothingToSave()
        log.info(
            "edit form save commit",
            extra={"context": {"entityType": self._entity_type, "fields": sorted(changes)}},
        )
        return CommitChanges(changes=changes, row_version=self._row_version)

    def saved(self, record: Mapping[str, Any]) -> None:
        """The PATCH landed: rebase on the returned record; the form stays open.

        Saving is not closing (edit_safety's rule) — the new values become the
        originals, so Cancel and the guard now answer relative to what was
        just committed, and the next Save carries the fresh ``rowVersion``.
        """
        self._original = {name: record.get(name) for name in self._editable}
        self._current = dict(self._original)
        self._row_version = int(record[ROW_VERSION_FIELD])
