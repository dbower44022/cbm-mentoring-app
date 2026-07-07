"""Column declarations for served grids: the ONE backend format vocabulary (FND-909 D1).

SKL-112 makes a column's FORMAT a view property: the server declares how a
column's values want to be rendered, and the client renders every cell
through one formatter keyed by that declaration — no consumer ever guesses
from the raw value. This module is the single backend home of that
vocabulary; the seeded mentor sources (:mod:`mentorapp.access.mentoring`,
:mod:`mentorapp.storage.triage`) declare their columns with it, and the
``/panels`` surface serves each column's ``format`` verbatim. The frontend
mirror is ``frontend/src/grid/format.ts`` — one canonical module per side,
never a third copy.

``displayed`` exists because a source may expose columns that are row
plumbing, not grid content: the REQ-019 ``userID`` scoping column and a
row's identity key (D2 — the triage view serves ``engagementID`` as the
row's ``recordId``, never as a rendered column). Non-displayed columns
still ride the SQL projection; they simply never become grid columns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from mentorapp.storage.theming import (
    CONDITION_OPERATORS,
    FORMATTING_EFFECTS,
    PRESENCE_OPERATORS,
    STATUS_COLOR_SLOTS,
)

# The closed set of format kinds a column may declare. Deliberately small:
# every kind names one client rendering (text verbatim, date "Jun 23, 2026",
# datetime "Jul 10, 10:00 AM", number as a plain numeral) — a new kind is a
# vocabulary change on BOTH sides, never a per-column improvisation.
# REQ-109 type-default justification; the served payload always carries a
# concrete alignment so every consumer renders identically. Doug's ruling:
# text/date/status LEFT, numbers CENTER (beats the right-align convention).
ALIGNMENT_DEFAULTS = {
    "text": "left",
    "date": "left",
    "datetime": "left",
    "number": "center",
}
COLUMN_ALIGNMENTS = ("left", "center", "right")

COLUMN_FORMATS: Final = frozenset({"text", "date", "datetime", "number"})


@dataclass(frozen=True)
class ColumnSpec:
    """One declared column of a served source: name, format, header, visibility.

    ``label`` is the ruled header wording when derivation would misspeak —
    ``lastSessionAt`` must read "Last Session" (the PI-010 ruling drops the
    "At"), which no camelCase split can produce; ``None`` lets the consumer
    derive the header from the field name.
    """

    field_name: str
    column_format: str = "text"
    label: str | None = None
    displayed: bool = True
    # REQ-109: justification defaults by data type (text/date/status LEFT,
    # number CENTER — Doug's explicit ruling over the right-align
    # convention); a view-level override wins when set.
    alignment: str | None = None

    def __post_init__(self) -> None:
        # Fail at declaration time, not render time: a typo'd format kind is
        # a source-controlled defect, so the module refuses to even load.
        if self.column_format not in COLUMN_FORMATS:
            raise ValueError(
                f"'{self.column_format}' is not a column format kind; "
                f"the vocabulary is {sorted(COLUMN_FORMATS)}."
            )


@dataclass(frozen=True)
class FormattingRuleSpec:
    """One source-controlled conditional-formatting rule of a served view (REQ-045).

    The same declaration path as :class:`ColumnSpec` (FND-909 D7): the seeded
    sources declare how their values want to be PAINTED next to how they want
    to be formatted, and the ``/panels`` surface serves both with the panel
    payload. The vocabulary is the persisted one in
    :mod:`mentorapp.storage.theming` — condition operators from
    ``CONDITION_OPERATORS``, the effect from ``FORMATTING_EFFECTS``, and the
    applied color an ``effect_slot`` naming a ``STATUS_COLOR_SLOTS`` slot,
    never a literal color (FND-906) — imported here, never re-declared, so a
    seeded rule and a template-authored ``conditionalFormattingRule`` row can
    never drift into different languages.

    Declared order IS the evaluation order: the client evaluates
    first-match-wins per target, exactly the ``evaluationOrder`` contract the
    theming surface serves for template rules.
    """

    condition_field: str
    condition_operator: str
    # A JSON scalar (string/number/bool); None for the presence operators.
    condition_value: str | float | bool | None
    effect: str
    effect_slot: str

    def __post_init__(self) -> None:
        # Fail at declaration time, not render time (the ColumnSpec stance):
        # a rule speaking outside the persisted vocabulary is a
        # source-controlled defect, so the module refuses to even load.
        if self.condition_operator not in CONDITION_OPERATORS:
            raise ValueError(
                f"'{self.condition_operator}' is not a condition operator; "
                f"the vocabulary is {sorted(CONDITION_OPERATORS)}."
            )
        if (self.condition_operator in PRESENCE_OPERATORS) != (self.condition_value is None):
            raise ValueError(
                f"'{self.condition_operator}' "
                + (
                    "tests the field itself; conditionValue must be None."
                    if self.condition_operator in PRESENCE_OPERATORS
                    else "compares against a value; conditionValue is required."
                )
            )
        if self.effect not in FORMATTING_EFFECTS:
            raise ValueError(
                f"'{self.effect}' is not a formatting effect; "
                f"the vocabulary is {sorted(FORMATTING_EFFECTS)}."
            )
        if self.effect_slot not in STATUS_COLOR_SLOTS:
            raise ValueError(
                f"'{self.effect_slot}' is not a status color slot; effects "
                f"name one of {sorted(STATUS_COLOR_SLOTS)}, never a literal "
                f"color (REQ-045, FND-906)."
            )


def exposed_field_names(columns: tuple[ColumnSpec, ...]) -> tuple[str, ...]:
    """Every declared field name, displayed or not — the SQL projection's truth.

    What ``dataSource.exposedFields`` stores (REQ-019 validates views against
    it), so it must cover the plumbing columns too.
    """
    return tuple(spec.field_name for spec in columns)


def displayed_columns(columns: tuple[ColumnSpec, ...]) -> tuple[ColumnSpec, ...]:
    """The columns a grid renders, in declared order — plumbing stays off-grid."""
    return tuple(spec for spec in columns if spec.displayed)
