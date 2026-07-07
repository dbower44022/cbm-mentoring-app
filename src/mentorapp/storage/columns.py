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

# The closed set of format kinds a column may declare. Deliberately small:
# every kind names one client rendering (text verbatim, date "Jun 23, 2026",
# datetime "Jul 10, 10:00 AM", number as a plain numeral) — a new kind is a
# vocabulary change on BOTH sides, never a per-column improvisation.
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

    def __post_init__(self) -> None:
        # Fail at declaration time, not render time: a typo'd format kind is
        # a source-controlled defect, so the module refuses to even load.
        if self.column_format not in COLUMN_FORMATS:
            raise ValueError(
                f"'{self.column_format}' is not a column format kind; "
                f"the vocabulary is {sorted(COLUMN_FORMATS)}."
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
