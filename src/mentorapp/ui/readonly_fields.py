"""Read-only field rendering & click-to-explain design (REQ-039, WTK-064).

No frontend shell exists yet (PI-002/PI-011), so — like ``record_preview``
and ``api.field_edit`` — this is executable design surface the shell renders
verbatim. On the full edit form, a field the user cannot edit is never
dropped, hidden, or grayed out: it appears in its usual position, rendered
exactly as the read view renders it, and clicking it produces an
educate-voice explanation of why it is not editable instead of an editor
(the forms standard's non-editable rule; educate-never-hide, applied to
fields).

The three non-editable kinds REQ-039 names, and where each is declared:

- **system** — the structural audit/version columns plus the entity's own ID.
  Detection is name-based (``STRUCTURAL_FIELDS`` + ``{entityType}ID``), the
  exact set the write engine rejects as ``readOnlyField`` and
  ``FieldEditors.open`` refuses outright, so what renders read-only can never
  disagree with what the API would bounce. Name-based matters: the registry
  seed deliberately excludes structural columns, so these fields never appear
  in ``GET /schema/{entity}`` — yet they still render on read surfaces.
- **computed** — declared on the field's registry row as
  ``visibilityHints.computed``. The registry row IS the field setting
  (REQ-033/REQ-040), and ``visibilityHints`` is its client-rendering-hint
  bucket, already served by the one metadata endpoint — so computedness
  reaches every form through the existing contract, no schema change, no
  second source to drift (DB-S6). No built-in field is computed today; the
  hint is the declared home for when one is.
- **permission** — a field-level access denial is the ACCESS layer's
  decision, handed in as a :class:`PermissionBlock` naming the missing
  permission and who grants it. This module never guesses at grants; it only
  guarantees the explanation names both (the no-access voice the grid
  standard fixes: which permission is missing, who grants it).

One explanation, both gestures: :func:`field_edit_reason` flattens a field's
:class:`~mentorapp.ui.auth_flows.EducateMessage` into the ``read_only_reason``
string the shell passes to ``FieldEditors.open`` — so a click on the edit
form and a double-click on the preview explain the field in the same words
(one canonical home; ``api.field_edit`` cannot import this module, so the
shell carries the text across).

Keyboard note (REQ-038): a read-only field is not a data input, so it takes
no Tab stop — :data:`READ_ONLY_RENDERING` declares that alongside the
in-place position and the click-explains gesture.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from mentorapp.api.records import STRUCTURAL_FIELDS
from mentorapp.observability import get_logger
from mentorapp.ui.auth_flows import EducateMessage

log = get_logger(__name__)

# The registry visibilityHints key that declares a computed field. Truthy
# value = computed; absent hints or key = an ordinary editable field.
COMPUTED_HINT: Final = "computed"

# The three REQ-039 kinds. Vocabulary validated in code, never a DB enum (DB-S7).
READ_ONLY_KINDS: Final[tuple[str, ...]] = ("computed", "system", "permission")


@dataclass(frozen=True)
class ReadOnlyRendering:
    """How every non-editable field renders on the edit form (REQ-039).

    Declared so the shell can never improvise: the field sits where it
    always sits (``inPlace``), shows the read view's formatted value
    (``readValue`` — no input control, no grayed-out editor), a click
    explains instead of opening an editor, and it takes no Tab stop
    (REQ-038: Tab stops only on editable fields).
    """

    position: str = "inPlace"
    value: str = "readValue"
    click: str = "explain"
    tab_stop: bool = False


READ_ONLY_RENDERING = ReadOnlyRendering()


@dataclass(frozen=True)
class PermissionBlock:
    """A field-level access denial, as the access layer reports it.

    Both names are mandatory because the explanation must say which
    permission is missing AND who grants it — "you can't" with no path
    forward is the anti-pattern the educate voice exists to kill.
    """

    permission_name: str
    granted_by: str


@dataclass(frozen=True)
class ReadOnlyField:
    """One non-editable field's complete rendering contract.

    ``kind`` is one of :data:`READ_ONLY_KINDS`; ``explanation`` is what the
    click shows, whole — never truncated to a tooltip.
    """

    field_name: str
    field_label: str
    kind: str
    explanation: EducateMessage
    rendering: ReadOnlyRendering = READ_ONLY_RENDERING


@dataclass(frozen=True)
class EditableField:
    """An ordinary editable field — the disposition's other half."""

    field_name: str
    field_label: str


# The system `why` repeats api.field_edit._STRUCTURAL_REASON verbatim:
# field_edit refuses structural fields on its own (api cannot import ui), so
# matching text here is what keeps the two gestures speaking one sentence.
# test_ui_readonly_fields pins the parity through field_edit's public API.
_SYSTEM_WHY: Final = "This is a system field; it is maintained automatically."


def _cannot_edit(field_label: str) -> str:
    return f"'{field_label}' can't be edited."


def computed_explanation(field_label: str) -> EducateMessage:
    """Why a computed field has no editor, and where its value comes from."""
    return EducateMessage(
        what_happened=_cannot_edit(field_label),
        why="Its value is calculated automatically from other information on the record.",
        what_next="Edit the fields it is calculated from — this value updates on its own.",
    )


def system_explanation(field_label: str) -> EducateMessage:
    """Why a structural/system field has no editor, anywhere, for anyone."""
    return EducateMessage(
        what_happened=_cannot_edit(field_label),
        why=_SYSTEM_WHY,
        what_next="No action is needed — the system keeps it up to date as the record changes.",
    )


def permission_explanation(field_label: str, block: PermissionBlock) -> EducateMessage:
    """Why THIS user can't edit the field: the missing grant, and who grants it."""
    return EducateMessage(
        what_happened=f"'{field_label}' can't be edited with your current access.",
        why=(
            f"Editing it requires the '{block.permission_name}' permission, "
            "which your account doesn't have."
        ),
        what_next=f"Ask {block.granted_by} to grant it if you need to change this field.",
    )


def classify_read_only(
    entity_type: str,
    field_name: str,
    field_label: str,
    *,
    visibility_hints: Mapping[str, Any] | None = None,
    permission_block: PermissionBlock | None = None,
) -> ReadOnlyField | None:
    """Decide whether one field renders read-only, and with which explanation.

    ``None`` means the field gets its normal editor. Precedence: system
    (nothing overrides the write engine), then computed (no one can edit it,
    a more complete answer than "you can't"), then permission. Inputs are the
    wire vocabulary — ``GET /schema/{entity}`` payload values plus the access
    layer's verdict — so the shell calls this straight off its loads.
    """
    if field_name in STRUCTURAL_FIELDS or field_name == f"{entity_type}ID":
        return ReadOnlyField(field_name, field_label, "system", system_explanation(field_label))
    if visibility_hints is not None and visibility_hints.get(COMPUTED_HINT):
        return ReadOnlyField(
            field_name, field_label, "computed", computed_explanation(field_label)
        )
    if permission_block is not None:
        return ReadOnlyField(
            field_name,
            field_label,
            "permission",
            permission_explanation(field_label, permission_block),
        )
    return None


def edit_form_disposition(
    entity_type: str,
    schema_fields: Sequence[Mapping[str, Any]],
    *,
    permission_blocks: Mapping[str, PermissionBlock] | None = None,
) -> tuple[EditableField | ReadOnlyField, ...]:
    """Map every schema field to its edit-form disposition, in given order.

    ``schema_fields`` is ``GET /schema/{entity}``'s ``data.fields`` verbatim;
    ``permission_blocks`` carries the access layer's field-level denials by
    ``fieldName``. Every field comes back — read-only ones as
    :class:`ReadOnlyField` in their usual position, never dropped (educate,
    never hide). Order is preserved as given: display order is the caller's
    job via ``visibilityHints``, exactly as on the read view.
    """
    blocks = permission_blocks or {}
    dispositions: list[EditableField | ReadOnlyField] = []
    for spec in schema_fields:
        field_name = str(spec["fieldName"])
        field_label = str(spec["fieldLabel"])
        read_only = classify_read_only(
            entity_type,
            field_name,
            field_label,
            visibility_hints=spec.get("visibilityHints"),
            permission_block=blocks.get(field_name),
        )
        if read_only is not None:
            dispositions.append(read_only)
        else:
            dispositions.append(EditableField(field_name, field_label))
    read_only_count = sum(1 for d in dispositions if isinstance(d, ReadOnlyField))
    log.info(
        "edit form disposition",
        extra={
            "context": {
                "entityType": entity_type,
                "fieldCount": len(dispositions),
                "readOnlyCount": read_only_count,
            }
        },
    )
    return tuple(dispositions)


def field_edit_reason(read_only: ReadOnlyField) -> str:
    """The ``read_only_reason`` string for ``FieldEditors.open`` (REQ-035).

    Flattens the click explanation so the preview's double-click refusal
    (:class:`~mentorapp.api.field_edit.FieldEditRefused`) speaks the same
    words as the edit form's click — one explanation per field, two gestures.
    """
    message = read_only.explanation
    return f"{message.why} {message.what_next}"
