"""Per-field edit window build: gesture bridge, control, frame (REQ-035, WTK-070).

No frontend shell exists yet (PI-002/PI-011), so — like ``edit_form`` and
``readonly_fields`` — this is executable design surface the shell renders
verbatim. Double-clicking a field's read-only element, anywhere records
render read-optimized, opens the SMALL self-contained editor with its own
Save and Cancel; this module builds the UI half of that window over the
lifecycle ``api.field_edit`` already owns.

What this module adds (and only this):

- **The frame** (:data:`FIELD_EDIT_FRAME`): the window's UI facts layered
  over the api-declared shape. Ctrl+S saves (Save's shortcut everywhere a
  Save exists); **Esc cancels** — the forms standard's per-field rule, and
  the one place Esc discards directly: Cancel IS this window's explicit
  discard, so Esc routes to ``FieldEditors.cancel`` with no guard. A bare
  window X is NOT Esc — it stays on ``request_close`` and the dirty guard.
  Focus starts in the editor control (the window's only editable field).
- **The gesture bridge** (:func:`open_from_double_click`): the shell hands
  the double-clicked field's schema spec straight to the bridge, which
  classifies read-onlyness via ``readonly_fields`` and carries the flattened
  explanation into ``FieldEditors.open`` — the "one explanation, both
  gestures" hand-off :func:`~mentorapp.ui.readonly_fields.field_edit_reason`
  exists for, now wired so the shell cannot forget it.
- **The control** (:func:`window_control`): the window hosts the SAME entry
  control the full edit form gives the field — rich-text fields get
  :data:`~mentorapp.ui.entry_editors.RICH_TEXT_CONTROL` (REQ-090: the one
  control at every rich-text entry point), ``"reference"`` fields get
  :data:`~mentorapp.ui.lookup_control.LOOKUP_CONTROL`, every other registry
  type its standard typed input. A small window is never a lesser editor.
- **The switch offer** (:func:`switch_offer_message`): the educate voice for
  :class:`~mentorapp.api.field_edit.FieldEditSwitch` — the layout standard's
  switch-to-that-window rule at field level, worded here so every surface
  offers the switch in the same words.

What it deliberately does NOT own (one canonical home each): the window
lifecycle, single-field PATCH, 409 routing, and dirty guard are
``api.field_edit``'s; which fields are read-only and their explanations are
``readonly_fields``'s; validation is ``api.form_validation``'s, run by the
shell on the one field exactly as on the full form. Structural fields are
refused by ``FieldEditors.open`` itself before any reason this bridge passes
— the registry seed excludes them from ``GET /schema/{entity}``, so they
cannot reach the bridge through a schema spec anyway.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from mentorapp.api.field_edit import (
    FIELD_EDIT_WINDOW,
    FieldEditOpened,
    FieldEditors,
    FieldEditRefused,
    FieldEditSwitch,
    FieldEditWindow,
)
from mentorapp.observability import get_logger
from mentorapp.ui.auth_flows import EducateMessage
from mentorapp.ui.edit_form import SAVE_SHORTCUT
from mentorapp.ui.entry_editors import RICH_TEXT_CONTROL, RichTextControl, is_rich_text
from mentorapp.ui.lookup_control import LOOKUP_CONTROL, LOOKUP_FIELD_TYPE, LookupControl
from mentorapp.ui.readonly_fields import (
    PermissionBlock,
    classify_read_only,
    field_edit_reason,
)

log = get_logger(__name__)

# The affordance label for FieldEditSwitch (layout standard, field level).
SWITCH_TO_WINDOW_LABEL: Final = "Switch to that window"


@dataclass(frozen=True)
class FieldEditFrame:
    """The per-field window's UI frame, layered over the api declaration.

    ``window`` is :data:`~mentorapp.api.field_edit.FIELD_EDIT_WINDOW`
    verbatim — small, own Save/Cancel, commits a single-field PATCH; this
    frame never restates those facts, it only adds the shell-side ones.
    ``escape`` is ``cancel`` (NOT the full form's ``requestLeave``): in this
    window Cancel is the explicit discard, so Esc may take it directly.
    """

    window: FieldEditWindow = FIELD_EDIT_WINDOW
    save_shortcut: str = SAVE_SHORTCUT
    escape: str = "cancel"
    initial_focus: str = "editorControl"


FIELD_EDIT_FRAME: Final = FieldEditFrame()


@dataclass(frozen=True)
class TypedInput:
    """The standard entry control for an ordinary registry field type.

    The same control the full edit form renders for the type; automatic
    formatting and validation come from the field's settings (REQ-033/034),
    never from this window.
    """

    field_type: str


def window_control(field_type: str) -> RichTextControl | LookupControl | TypedInput:
    """The one entry control this window hosts, by registry ``fieldType``.

    Routes the two types with canonical controls to them (REQ-090's
    rich-text rule; the lookup standard for ``"reference"``) so the small
    window can never host a lesser editor than the full form.
    """
    if is_rich_text(field_type):
        return RICH_TEXT_CONTROL
    if field_type == LOOKUP_FIELD_TYPE:
        return LOOKUP_CONTROL
    return TypedInput(field_type)


@dataclass(frozen=True)
class OpenFieldWindow:
    """One opened per-field window, ready to render.

    ``opened`` carries the lifecycle facts (base value, ``rowVersion``,
    ownership of the field's only editor); ``control`` is what the window
    hosts; ``frame`` is how it behaves. Save/Cancel/close route to the
    owning :class:`~mentorapp.api.field_edit.FieldEditors` by ``window_key``.
    """

    opened: FieldEditOpened
    field_label: str
    control: RichTextControl | LookupControl | TypedInput
    frame: FieldEditFrame = FIELD_EDIT_FRAME


def open_from_double_click(
    editors: FieldEditors,
    window_key: str,
    entity_type: str,
    field_spec: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    permission_block: PermissionBlock | None = None,
) -> OpenFieldWindow | FieldEditSwitch | FieldEditRefused:
    """The double-click gesture: open the field's small editor, or explain.

    ``field_spec`` is the double-clicked field's ``GET /schema/{entity}``
    entry verbatim (``fieldName``/``fieldLabel``/``fieldType`` plus
    ``visibilityHints``); ``record`` is the wire-shaped copy the element
    rendered from; ``permission_block`` is the access layer's field-level
    verdict, exactly as on the full form. A read-only field comes back as
    :class:`FieldEditRefused` speaking the same words the edit form's click
    shows; a field already being edited comes back as
    :class:`FieldEditSwitch` (render :func:`switch_offer_message`).
    """
    field_name = str(field_spec["fieldName"])
    field_label = str(field_spec["fieldLabel"])
    read_only = classify_read_only(
        entity_type,
        field_name,
        field_label,
        visibility_hints=field_spec.get("visibilityHints"),
        permission_block=permission_block,
    )
    reason = field_edit_reason(read_only) if read_only is not None else None
    outcome = editors.open(
        window_key, entity_type, field_name, dict(record), read_only_reason=reason
    )
    if isinstance(outcome, FieldEditRefused):
        log.info(
            "field edit double-click refused",
            extra={"context": {"entityType": entity_type, "fieldName": field_name}},
        )
        return outcome
    if isinstance(outcome, FieldEditSwitch):
        return outcome
    return OpenFieldWindow(
        opened=outcome,
        field_label=field_label,
        control=window_control(str(field_spec["fieldType"])),
    )


def switch_offer_message(field_label: str) -> EducateMessage:
    """The switch offer for a field already open in another of the user's windows."""
    return EducateMessage(
        what_happened=f"'{field_label}' is already open for editing in another window.",
        why=(
            "Editing the same field in two windows at once would put your own "
            "changes in conflict with each other."
        ),
        what_next=(
            f"Use '{SWITCH_TO_WINDOW_LABEL}' to continue where that edit left off — "
            "nothing you typed there is lost."
        ),
    )
