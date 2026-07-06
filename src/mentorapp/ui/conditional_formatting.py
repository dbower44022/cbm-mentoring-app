"""Render-time grid formatting (WTK-117): REQ-044 precedence applied per grid,
REQ-045 rules evaluated first-match-wins per target.

The render-time engine a grid consumer calls with what it already has — the
three theme layers for its grid, the active template's rules in declared
order, and each row's record — and gets back the exact colors to paint. It
COMPOSES the settled semantics and re-decides none of them:

- **Precedence is WTK-112's:** :func:`resolve_grid_render` resolves the
  org-default → user-choice → row-theme layering through
  :func:`mentorapp.ui.theming.resolve_effective_grid_theme` — the one
  implementation — once per grid, then reuses that
  :class:`~mentorapp.ui.theming.EffectiveGridTheme` for every row.
- **First match wins PER TARGET (REQ-045):** rules evaluate in declared
  order — the sequence the caller passes IS ``evaluationOrder`` (the WTK-111
  ordered association; the WTK-114 reorder verb keeps it a clean
  permutation). Each :data:`~mentorapp.storage.theming.FORMATTING_EFFECTS`
  target is painted by the FIRST rule that both names it and matches;
  later rules never stack on an already-painted target but still compete
  for the targets that remain open.
- **Effects name status slots, never colors (REQ-045/FND-906):** a match
  answers an ``effectSlot`` and :func:`resolve_row_render` reads the color
  from the effective theme's slot — so switching templates recolors every
  rule coherently, and no literal color can enter through evaluation.
- **The row-background pile (:data:`ROW_BACKGROUND_PRECEDENCE`):** selection
  outranks a matched rule, a matched rule outranks banding — the ordering
  WTK-207 left to this module (banding is boundary clarity, never a signal,
  so it colors exactly the rows nothing else claimed). Selection replaces
  the row's background AND text together: the selected pair ships curated
  for readability, and a rule's status text on ``selectedRowBackground``
  would gamble it away.
- **Malformed rules fail loudly, missing data never does:** rule STRUCTURE
  (unknown operator/effect/slot, a comparison without a value) is a contract
  violation and raises :class:`~mentorapp.ui.theming.ThemingError` before
  any row renders — the WTK-114 surface refuses such writes, so reaching
  render with one is a bug. Row DATA is the user's, so a condition over a
  missing or type-mismatched value simply does not match: an absent value
  matches only the presence operators (the SQL ``NULL`` posture — unknown
  is neither equal nor unequal), and ordering across unlike types is no
  match rather than a crash.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from mentorapp.storage.theming import (
    CONDITION_OPERATORS,
    FORMATTING_EFFECTS,
    PRESENCE_OPERATORS,
    STATUS_COLOR_SLOTS,
)
from mentorapp.ui.row_banding import BAND_BASE, band_for_position
from mentorapp.ui.theming import (
    EffectiveGridTheme,
    ThemeLayers,
    ThemingError,
    resolve_effective_grid_theme,
)

# Who decided a rendered row part — extends the WTK-112 layer provenance
# vocabulary downward into the row: the theme layers decide the palette,
# these decide which palette entry a given row shows.
DECIDED_BY_SELECTION: Final = "selection"
DECIDED_BY_RULE: Final = "formattingRule"
DECIDED_BY_BANDING: Final = "banding"
DECIDED_BY_TEMPLATE: Final = "template"

# The row-background pile, top first (REQ-045/REQ-092): selection is the
# strongest signal, a matched formatting rule signals under it, banding is
# the floor. WTK-207's effective_row_background already puts banding at the
# bottom; this tuple is the canonical statement of what stacks above it.
ROW_BACKGROUND_PRECEDENCE: Final[tuple[str, ...]] = (
    DECIDED_BY_SELECTION,
    DECIDED_BY_RULE,
    DECIDED_BY_BANDING,
)

_RULE_KEYS: Final = frozenset(
    {"conditionField", "conditionOperator", "conditionValue", "effect", "effectSlot"}
)


def validate_formatting_rule(rule: Mapping[str, Any]) -> None:
    """Reject a structurally invalid rule before any row evaluates it.

    The render-time twin of the WTK-114 write validation (the ``rule_errors``
    accumulator): same persisted vocabularies, but fail-fast with
    :class:`ThemingError` — the ``validate_template``/``color_slot_errors``
    precedent for the ui/api split. Raises on an unknown key, operator,
    effect, or effect slot; on a presence operator carrying a value; and on
    a comparison operator missing one.
    """
    unknown = sorted(set(rule) - _RULE_KEYS)
    if unknown:
        raise ThemingError(f"unknown rule keys: {unknown}")
    condition_field = rule.get("conditionField")
    if not isinstance(condition_field, str) or not condition_field.strip():
        raise ThemingError("conditionField must name a field of the grid's data source")
    operator = rule.get("conditionOperator")
    if operator not in CONDITION_OPERATORS:
        raise ThemingError(f"conditionOperator must be one of {sorted(CONDITION_OPERATORS)}")
    value = rule.get("conditionValue")
    if operator in PRESENCE_OPERATORS:
        if value is not None:
            raise ThemingError(f"'{operator}' tests the field itself; conditionValue is null")
    elif value is None:
        raise ThemingError(f"'{operator}' compares against a value; conditionValue is required")
    if rule.get("effect") not in FORMATTING_EFFECTS:
        raise ThemingError(f"effect must repaint a fixed slot {sorted(FORMATTING_EFFECTS)}")
    if rule.get("effectSlot") not in STATUS_COLOR_SLOTS:
        raise ThemingError(
            f"effectSlot must name a status color slot {sorted(STATUS_COLOR_SLOTS)}, "
            "never a literal color"
        )


def _is_empty(value: Any) -> bool:
    # A data-source field is absent (None) or blank — a whitespace-only
    # string is still data the user typed, so it counts as present.
    return value is None or value == ""


def _scalars_equal(field_value: Any, comparand: Any) -> bool:
    # Python would happily say True == 1; a bool field and a numeric
    # comparand (or vice versa) are a type mismatch, not a match.
    if isinstance(field_value, bool) != isinstance(comparand, bool):
        return False
    return bool(field_value == comparand)


def _ordering_holds(field_value: Any, comparand: Any, *, greater: bool) -> bool:
    both_numbers = (
        isinstance(field_value, (int, float))
        and isinstance(comparand, (int, float))
        and not isinstance(field_value, bool)
        and not isinstance(comparand, bool)
    )
    # Strings order lexicographically — how ISO dates and datetimes travel
    # the read surface, so date rules work without a date type on the wire.
    both_strings = isinstance(field_value, str) and isinstance(comparand, str)
    if not (both_numbers or both_strings):
        return False
    return field_value > comparand if greater else field_value < comparand


def condition_matches(rule: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    """Whether one VALIDATED rule's condition holds for one row's record.

    ``record`` maps data-source field names to wire values (JSON scalars);
    a field the record does not carry evaluates as absent. Never raises on
    data: absent values match only the presence operators, and unlike-type
    comparisons are simply no match.
    """
    field_value = record.get(rule["conditionField"])
    operator: str = rule["conditionOperator"]
    if operator == "isEmpty":
        return _is_empty(field_value)
    if operator == "isNotEmpty":
        return not _is_empty(field_value)
    if field_value is None:
        return False
    comparand = rule["conditionValue"]
    if operator == "equals":
        return _scalars_equal(field_value, comparand)
    if operator == "notEquals":
        return not _scalars_equal(field_value, comparand)
    if operator == "greaterThan":
        return _ordering_holds(field_value, comparand, greater=True)
    if operator == "lessThan":
        return _ordering_holds(field_value, comparand, greater=False)
    # 'contains' — the last CONDITION_OPERATORS member; validation already
    # guaranteed a string comparand, and a non-string field is no match.
    return isinstance(field_value, str) and str(rule["conditionValue"]) in field_value


@dataclass(frozen=True)
class RuleMatch:
    """One won target: which status slot paints it, and which rule decided.

    ``rule_position`` is the 1-based declared position — the honest "why is
    this row orange" answer, same posture as the theme provenance record.
    """

    target: str
    effect_slot: str
    rule_position: int


def evaluate_formatting_rules(
    rules: Sequence[Mapping[str, Any]], record: Mapping[str, Any]
) -> dict[str, RuleMatch]:
    """The REQ-045 evaluation: declared order, first match wins per target.

    Walks ``rules`` in the order given (the persisted ``evaluationOrder``);
    the first rule that matches AND names a still-open target claims it —
    an already-painted target never repaints, targets no rule matches stay
    absent from the result. Raises :class:`ThemingError` on a structurally
    invalid rule; never raises on row data.
    """
    for rule in rules:
        validate_formatting_rule(rule)
    return _first_match_per_target(rules, record)


def _first_match_per_target(
    rules: Sequence[Mapping[str, Any]], record: Mapping[str, Any]
) -> dict[str, RuleMatch]:
    # The evaluation core over already-validated rules — the per-row path
    # (resolve_row_render rides a plan whose rules validated once).
    matches: dict[str, RuleMatch] = {}
    for position, rule in enumerate(rules, start=1):
        target: str = rule["effect"]
        if target in matches or not condition_matches(rule, record):
            continue
        matches[target] = RuleMatch(
            target=target, effect_slot=rule["effectSlot"], rule_position=position
        )
        if len(matches) == len(FORMATTING_EFFECTS):
            break
    return matches


@dataclass(frozen=True)
class GridRenderPlan:
    """One grid's settled render inputs: the effective theme plus its rules.

    Built once per grid by :func:`resolve_grid_render` so the layering and
    rule validation never repeat per row; every row then renders through
    :func:`resolve_row_render` against this plan.
    """

    theme: EffectiveGridTheme
    rules: tuple[Mapping[str, Any], ...]


def resolve_grid_render(
    layers: ThemeLayers, rules: Sequence[Mapping[str, Any]] = ()
) -> GridRenderPlan:
    """Settle everything grid-wide before the first row paints (REQ-044).

    Applies the three-layer precedence through the one WTK-112 resolver and
    validates the active template's rules (declared order preserved). Raises
    :class:`ThemingError` on any malformed layer or rule — a grid renders
    from a fully valid plan or not at all, never half-styled.
    """
    theme = resolve_effective_grid_theme(layers)
    for rule in rules:
        validate_formatting_rule(rule)
    return GridRenderPlan(theme=theme, rules=tuple(rules))


@dataclass(frozen=True)
class RowRender:
    """What one row actually paints, with the deciding concern per part.

    ``provenance`` maps ``rowBackground``/``rowText``/``accent`` to a
    ``DECIDED_BY_*`` value; ``accent`` is ``None`` unless a rule painted it
    (a row carries no accent marker by default). ``matches`` keeps the
    winning :class:`RuleMatch` per target for the explanation surface.
    """

    background: str
    text: str
    accent: str | None
    provenance: dict[str, str]
    matches: dict[str, RuleMatch]


def resolve_row_render(
    plan: GridRenderPlan,
    record: Mapping[str, Any],
    position: int,
    *,
    selected: bool = False,
) -> RowRender:
    """One DATA row's colors: selection, then matched rules, then banding.

    ``position`` is the 1-based rendered data-row position (structural rows
    never consume one — WTK-207's cadence). Effect colors resolve from the
    plan's effective theme by status slot, so a matched rule paints WITH the
    theme; selection replaces background and text together per the module
    ruling above.
    """
    matches = _first_match_per_target(plan.rules, record)
    colors = plan.theme.colors
    if selected:
        background, background_by = colors["selectedRowBackground"], DECIDED_BY_SELECTION
        text, text_by = colors["selectedRowText"], DECIDED_BY_SELECTION
    else:
        if "rowBackground" in matches:
            background = colors[matches["rowBackground"].effect_slot]
            background_by = DECIDED_BY_RULE
        else:
            band = band_for_position(position)
            background = colors[
                "rowBackground" if band == BAND_BASE else "rowAlternateBackground"
            ]
            background_by = DECIDED_BY_BANDING
        if "rowText" in matches:
            text, text_by = colors[matches["rowText"].effect_slot], DECIDED_BY_RULE
        else:
            text, text_by = colors["rowText"], DECIDED_BY_TEMPLATE
    accent: str | None = None
    provenance = {"rowBackground": background_by, "rowText": text_by}
    if "accent" in matches:
        accent = colors[matches["accent"].effect_slot]
        provenance["accent"] = DECIDED_BY_RULE
    return RowRender(
        background=background,
        text=text,
        accent=accent,
        provenance=provenance,
        matches=matches,
    )
