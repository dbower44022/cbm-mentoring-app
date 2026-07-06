"""Form keyboard standard: focus, tab order, key dispatch (REQ-038, WTK-075).

No frontend shell exists yet (PI-002/PI-011), so — like ``edit_form`` and
``field_edit_window`` — this is executable design surface the shell renders
verbatim. The forms standard's keyboard rules already live one fact at a
time in their owning frames (``SAVE_SHORTCUT``, the full form's
``escape="requestLeave"``, the per-field window's ``escape="cancel"``,
read-only fields' ``tab_stop=False``); this module is the one place those
facts become a working keyboard: the restricted tab cycle, initial focus,
and the key dispatch that wires Ctrl+S and Escape to the form's own save
and dirty-guard answers.

What this module owns (and only this):

- **The restricted tab cycle** (:func:`tab_order`, :func:`next_tab_stop`):
  Tab stops ONLY on editable fields — never labels, read-only/computed
  elements, or other non-data-input controls (Save and Cancel included;
  Ctrl+S is the keyboard's path to Save). The cycle wraps because there is
  no other legitimate stop to fall off to.
- **Initial focus** (:func:`initial_focus`): the first tab stop — by
  construction the same field :func:`~mentorapp.ui.edit_form.first_editable_field`
  answers, pinned by test. ``None`` (a fully read-only form has no stops)
  falls to the Save row, exactly as ``first_editable_field`` documents.
- **The key dispatch** (:func:`form_key`): Ctrl+S routes to
  :meth:`~mentorapp.ui.edit_form.EditForm.request_save`, Escape to
  :meth:`~mentorapp.ui.edit_form.EditForm.request_leave` — so a keyboard
  Escape meets the SAME dirty guard as any other navigation, never a
  bypass — and Enter is :class:`ActivateFocusedControl`: on a multi-field
  form Enter NEVER submits, it acts on the focused control only.

What it deliberately does NOT own (one canonical home each): the per-field
window's Esc-cancels exception is ``field_edit_window``'s
(:data:`~mentorapp.ui.field_edit_window.FIELD_EDIT_FRAME` routes Esc to
``FieldEditors.cancel`` — Cancel IS that window's explicit discard);
which fields are editable is ``readonly_fields``'s disposition; what Save
and leave DO is ``edit_form``'s. The credential screens' Enter-submits is a
MARKED DEVIATION owned by ``auth_flows`` (single-purpose forms, universal
convention) — it never weakens this rule for data forms.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from mentorapp.ui.edit_form import (
    EDIT_FORM_SCREEN,
    SAVE_SHORTCUT,
    CommitChanges,
    DirtyLeaveGuard,
    EditForm,
    LeaveAllowed,
    NothingToSave,
)
from mentorapp.ui.field_edit_window import FIELD_EDIT_FRAME
from mentorapp.ui.readonly_fields import EditableField, ReadOnlyField

# The keys this standard claims; everything else stays the shell's/browser's.
ESCAPE_KEY: Final = "Escape"
ENTER_KEY: Final = "Enter"
TAB_KEY: Final = "Tab"
SHIFT_TAB_KEY: Final = "Shift+Tab"


@dataclass(frozen=True)
class FormKeyboardModel:
    """The forms keyboard in one declaration (REQ-038), for the shell to render.

    Every slot restates nothing: it reads the owning frame's fact
    (``EDIT_FORM_SCREEN``, ``FIELD_EDIT_FRAME``, ``SAVE_SHORTCUT``), so the
    model can never drift from the surfaces it describes.
    """

    save: str = SAVE_SHORTCUT
    escape_full_form: str = EDIT_FORM_SCREEN.escape
    escape_per_field_window: str = FIELD_EDIT_FRAME.escape
    enter: str = "activateFocusedControl"
    tab: str = "nextEditableField"
    shift_tab: str = "previousEditableField"
    initial_focus: str = EDIT_FORM_SCREEN.initial_focus


FORM_KEYBOARD_MODEL: Final = FormKeyboardModel()


@dataclass(frozen=True)
class ActivateFocusedControl:
    """Enter's whole meaning on a multi-field form: the focused control acts.

    Never a submit — a dropdown opens, a checkbox toggles, a rich-text
    editor takes the newline. Declared as an outcome so the shell has no
    default-submit path left to fall into.
    """


def tab_order(
    dispositions: Sequence[EditableField | ReadOnlyField],
) -> tuple[EditableField, ...]:
    """The form's complete tab cycle: editable fields only, in display order.

    Read-only fields keep their position on screen (``READ_ONLY_RENDERING``)
    but take no stop; empty means the whole form rendered read-only and
    focus falls to the Save row.
    """
    return tuple(d for d in dispositions if isinstance(d, EditableField))


def initial_focus(
    dispositions: Sequence[EditableField | ReadOnlyField],
) -> EditableField | None:
    """Where focus starts: the first tab stop (the first editable field)."""
    stops = tab_order(dispositions)
    return stops[0] if stops else None


def next_tab_stop(
    dispositions: Sequence[EditableField | ReadOnlyField],
    focused_field: str | None,
    *,
    backwards: bool = False,
) -> EditableField | None:
    """Resolve one Tab (or Shift+Tab): the next stop in the wrapping cycle.

    ``focused_field`` is the field holding focus by ``fieldName``; ``None``
    or a name outside the cycle (focus was on Save, a label, a read-only
    element) re-enters at the first stop — last when ``backwards``, so
    Shift+Tab from the Save row lands on the last field, not the first.
    ``None`` out means there are no stops at all.
    """
    stops = tab_order(dispositions)
    if not stops:
        return None
    names = [stop.field_name for stop in stops]
    if focused_field not in names:
        return stops[-1] if backwards else stops[0]
    step = -1 if backwards else 1
    return stops[(names.index(focused_field) + step) % len(stops)]


# What one full-form keypress can come back as (None = not the form's key).
type FormKeyOutcome = (
    NothingToSave | CommitChanges | LeaveAllowed | DirtyLeaveGuard | ActivateFocusedControl
)


def form_key(form: EditForm, key: str) -> FormKeyOutcome | None:
    """Dispatch one full-form keypress to the form's own answer.

    Ctrl+S asks the form to save (``NothingToSave`` when clean, the minimal
    ``CommitChanges`` when dirty); Escape asks to leave, so the dirty guard
    runs exactly as it would for any navigation (``LeaveAllowed`` /
    ``DirtyLeaveGuard``); Enter is :class:`ActivateFocusedControl`, never a
    submit. ``None`` means the key is not the form's — the shell handles it
    normally (Tab resolves through :func:`next_tab_stop`, which needs the
    focus position this dispatcher deliberately doesn't track).
    """
    if key == SAVE_SHORTCUT:
        return form.request_save()
    if key == ESCAPE_KEY:
        return form.request_leave()
    if key == ENTER_KEY:
        return ActivateFocusedControl()
    return None
