"""Subtle field-level help affordance design (REQ-040, WTK-077).

No frontend shell exists yet (PI-002/PI-011), so — like ``readonly_fields``
and ``edit_form`` — this is executable design surface the shell renders
verbatim. Field settings carry optional admin-maintained help text (the
registry row IS the field setting, REQ-033/REQ-040; the ``helpText`` column
lands with migration 0008 and reaches the client through the one metadata
endpoint, ``GET /schema/{entity}`` — no second contract to drift, DB-S6).
This module fixes how that text renders: an info marker on the field label,
the text revealed on hover or focus, and NOTHING when no help text exists —
never a permanent hint paragraph, never an empty marker.

This is the bottom layer of the Help pyramid (field level, below the
educate-voice messages and the page-level URL-mapped help). It is
admin-maintained data, not hardcoded copy: this module never authors help
text, it only carries what the schema payload delivers.

Keyboard note (REQ-038): the marker is not a data input, so it takes no Tab
stop. The focus reveal rides the field's own editor — when the editor gains
focus the help shows — so keyboard users meet the help without a dedicated
stop, and hover serves the mouse.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from mentorapp.observability import get_logger

log = get_logger(__name__)

# The schema-payload key carrying the field setting's help text, exactly as
# GET /schema/{entity} serves it (camelCase wire vocabulary).
HELP_TEXT_KEY: Final = "helpText"


@dataclass(frozen=True)
class HelpMarkerRendering:
    """How field-level help renders wherever a field label appears (REQ-040).

    Declared so the shell can never improvise: a small info marker beside
    the label (``fieldLabel``), the text revealed on hovering the marker or
    on the field's editor gaining focus, never rendered as a persistent
    paragraph, and no Tab stop (REQ-038: Tab stops only on editable fields).
    """

    marker: str = "infoMarker"
    placement: str = "fieldLabel"
    reveal: tuple[str, ...] = ("hover", "focus")
    persistent: bool = False
    tab_stop: bool = False


HELP_MARKER_RENDERING = HelpMarkerRendering()


@dataclass(frozen=True)
class FieldHelp:
    """One field's help affordance: the marker plus the text it reveals.

    ``help_text`` is the admin's words verbatim — the affordance carries the
    field setting, it never rewrites it.
    """

    field_name: str
    field_label: str
    help_text: str
    rendering: HelpMarkerRendering = HELP_MARKER_RENDERING


def field_help(spec: Mapping[str, Any]) -> FieldHelp | None:
    """Decide whether one field gets the help marker, from its schema payload.

    ``spec`` is one ``GET /schema/{entity}`` ``data.fields`` entry verbatim.
    ``None`` means render nothing — no marker, no placeholder. Absent key,
    null, and whitespace-only all count as "no help text": an admin clearing
    the setting must remove the marker, and a marker that reveals blankness
    would be worse than none.
    """
    raw = spec.get(HELP_TEXT_KEY)
    if raw is None or not str(raw).strip():
        return None
    return FieldHelp(
        field_name=str(spec["fieldName"]),
        field_label=str(spec["fieldLabel"]),
        help_text=str(raw),
    )


def form_help_affordances(schema_fields: Sequence[Mapping[str, Any]]) -> dict[str, FieldHelp]:
    """Map a form's fields to their help affordances, keyed by ``fieldName``.

    ``schema_fields`` is ``GET /schema/{entity}``'s ``data.fields`` verbatim.
    Only fields with help text appear — the shell attaches a marker per key
    and renders nothing for the rest. Keyed (not ordered) because the marker
    rides each label wherever the field renders: full edit form, per-field
    edit window, and create form all attach from the same mapping.
    """
    affordances: dict[str, FieldHelp] = {}
    for spec in schema_fields:
        help_ = field_help(spec)
        if help_ is not None:
            affordances[help_.field_name] = help_
    log.info(
        "field help affordances",
        extra={
            "context": {
                "fieldCount": len(schema_fields),
                "withHelpCount": len(affordances),
            }
        },
    )
    return affordances
