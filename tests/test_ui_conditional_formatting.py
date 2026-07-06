"""Tests for the render-time formatting engine (WTK-117, REQ-044/REQ-045)."""

from __future__ import annotations

from typing import Any

import pytest

from mentorapp.storage.theming import CONDITION_OPERATORS, PRESENCE_OPERATORS
from mentorapp.ui.conditional_formatting import (
    DECIDED_BY_BANDING,
    DECIDED_BY_RULE,
    DECIDED_BY_SELECTION,
    DECIDED_BY_TEMPLATE,
    ROW_BACKGROUND_PRECEDENCE,
    RuleMatch,
    condition_matches,
    evaluate_formatting_rules,
    resolve_grid_render,
    resolve_row_render,
    validate_formatting_rule,
)
from mentorapp.ui.theming import STANDARD_TEMPLATE, ThemeLayers, ThemingError


def _rule(**overrides: Any) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "conditionField": "sessionStatus",
        "conditionOperator": "equals",
        "conditionValue": "overdue",
        "effect": "rowBackground",
        "effectSlot": "statusNegative",
    }
    rule.update(overrides)
    return rule


def _plan(*rules: dict[str, Any]) -> Any:
    return resolve_grid_render(ThemeLayers(org_default=STANDARD_TEMPLATE), rules)


# --- Rule structure: contract errors fail loudly ------------------------------------


def test_rule_vocabulary_is_the_persisted_one() -> None:
    assert set(PRESENCE_OPERATORS) < set(CONDITION_OPERATORS)
    validate_formatting_rule(_rule())


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"surprise": True}, "unknown rule keys"),
        ({"conditionField": "  "}, "conditionField"),
        ({"conditionOperator": "regexMatch"}, "conditionOperator"),
        ({"conditionOperator": "isEmpty"}, "tests the field itself"),
        ({"conditionValue": None}, "conditionValue is required"),
        ({"effect": "panelBackground"}, "effect must repaint"),
        ({"effectSlot": "#ff0000"}, "never a literal color"),
        ({"effectSlot": "accent"}, "status color slot"),
    ],
)
def test_malformed_rules_are_contract_errors(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(ThemingError, match=match):
        validate_formatting_rule(_rule(**overrides))
    with pytest.raises(ThemingError, match=match):
        resolve_grid_render(ThemeLayers(org_default=STANDARD_TEMPLATE), [_rule(**overrides)])


# --- Operator semantics: data never raises -------------------------------------------


def test_equals_and_not_equals_guard_types() -> None:
    assert condition_matches(_rule(), {"sessionStatus": "overdue"})
    assert not condition_matches(_rule(), {"sessionStatus": "scheduled"})
    assert condition_matches(
        _rule(conditionOperator="notEquals"), {"sessionStatus": "scheduled"}
    )
    # bool and number are a type mismatch, not Python's True == 1.
    assert not condition_matches(_rule(conditionValue=1), {"sessionStatus": True})
    assert condition_matches(
        _rule(conditionOperator="notEquals", conditionValue=1), {"sessionStatus": True}
    )


def test_absent_values_match_only_the_presence_operators() -> None:
    # The SQL NULL posture: unknown is neither equal nor unequal.
    for operator in ("equals", "notEquals", "greaterThan", "lessThan", "contains"):
        rule = _rule(conditionOperator=operator, conditionValue="x")
        assert not condition_matches(rule, {})
        assert not condition_matches(rule, {"sessionStatus": None})
    empty = _rule(conditionOperator="isEmpty", conditionValue=None)
    assert condition_matches(empty, {})
    assert condition_matches(empty, {"sessionStatus": None})
    assert condition_matches(empty, {"sessionStatus": ""})
    assert not condition_matches(empty, {"sessionStatus": "  "})
    filled = _rule(conditionOperator="isNotEmpty", conditionValue=None)
    assert condition_matches(filled, {"sessionStatus": "scheduled"})
    assert not condition_matches(filled, {"sessionStatus": None})


def test_ordering_compares_numbers_and_strings_never_mixed_types() -> None:
    greater = _rule(conditionOperator="greaterThan", conditionValue=3)
    assert condition_matches(greater, {"sessionStatus": 4})
    assert condition_matches(greater, {"sessionStatus": 3.5})
    assert not condition_matches(greater, {"sessionStatus": 3})
    assert not condition_matches(greater, {"sessionStatus": True})
    assert not condition_matches(greater, {"sessionStatus": "4"})
    # ISO dates travel as strings and order lexicographically.
    before = _rule(conditionOperator="lessThan", conditionValue="2026-07-01")
    assert condition_matches(before, {"sessionStatus": "2026-06-30"})
    assert not condition_matches(before, {"sessionStatus": "2026-07-02"})


def test_contains_is_string_containment() -> None:
    rule = _rule(conditionOperator="contains", conditionValue="urgent")
    assert condition_matches(rule, {"sessionStatus": "very urgent indeed"})
    assert not condition_matches(rule, {"sessionStatus": "Urgent"})
    assert not condition_matches(rule, {"sessionStatus": 12345})


# --- First match wins per target (REQ-045) -------------------------------------------


def test_first_match_wins_per_target_in_declared_order() -> None:
    rules = [
        _rule(conditionValue="scheduled", effectSlot="statusNegative"),
        _rule(effectSlot="statusWarning"),  # first MATCH for rowBackground
        _rule(effectSlot="statusPositive"),  # later match never repaints
        _rule(effect="rowText", effectSlot="statusNegative"),  # open target still paints
    ]
    matches = evaluate_formatting_rules(rules, {"sessionStatus": "overdue"})
    assert matches == {
        "rowBackground": RuleMatch(
            target="rowBackground", effect_slot="statusWarning", rule_position=2
        ),
        "rowText": RuleMatch(target="rowText", effect_slot="statusNegative", rule_position=4),
    }


def test_no_matching_rule_leaves_targets_unpainted() -> None:
    assert evaluate_formatting_rules([_rule()], {"sessionStatus": "scheduled"}) == {}
    assert evaluate_formatting_rules([], {"sessionStatus": "overdue"}) == {}


# --- Render-time composition: precedence per grid, then the row pile -----------------


def test_grid_plan_applies_the_three_layers_once() -> None:
    row_theme = {"colors": {"rowBackground": "#111111", "rowAlternateBackground": "#222222"}}
    plan = resolve_grid_render(
        ThemeLayers(org_default=STANDARD_TEMPLATE, row_theme=row_theme), [_rule()]
    )
    assert plan.theme.colors["rowBackground"] == "#111111"
    assert (
        plan.theme.colors["headerBackground"] == STANDARD_TEMPLATE["colors"]["headerBackground"]
    )
    # Unformatted rows band with the layered pair: odd base, even alternate.
    first = resolve_row_render(plan, {"sessionStatus": "scheduled"}, 1)
    second = resolve_row_render(plan, {"sessionStatus": "scheduled"}, 2)
    assert (first.background, second.background) == ("#111111", "#222222")
    assert first.provenance["rowBackground"] == DECIDED_BY_BANDING
    assert first.text == plan.theme.colors["rowText"]
    assert first.provenance["rowText"] == DECIDED_BY_TEMPLATE
    assert first.accent is None and "accent" not in first.provenance


def test_matched_effects_paint_status_slots_from_the_theme() -> None:
    plan = _plan(_rule(), _rule(effect="accent", effectSlot="statusWarning"))
    row = resolve_row_render(plan, {"sessionStatus": "overdue"}, 2)
    assert row.background == plan.theme.colors["statusNegative"]
    assert row.provenance["rowBackground"] == DECIDED_BY_RULE
    assert row.accent == plan.theme.colors["statusWarning"]
    assert row.provenance["accent"] == DECIDED_BY_RULE
    assert row.matches["rowBackground"].rule_position == 1


def test_selection_outranks_rules_and_banding_for_background_and_text() -> None:
    assert ROW_BACKGROUND_PRECEDENCE == (
        DECIDED_BY_SELECTION,
        DECIDED_BY_RULE,
        DECIDED_BY_BANDING,
    )
    plan = _plan(_rule(), _rule(effect="rowText", effectSlot="statusWarning"))
    row = resolve_row_render(plan, {"sessionStatus": "overdue"}, 1, selected=True)
    assert row.background == plan.theme.colors["selectedRowBackground"]
    assert row.text == plan.theme.colors["selectedRowText"]
    assert row.provenance["rowBackground"] == DECIDED_BY_SELECTION
    assert row.provenance["rowText"] == DECIDED_BY_SELECTION
    # The matches are still evaluated and reported — only the paint defers.
    assert set(row.matches) == {"rowBackground", "rowText"}


def test_switching_templates_recolors_every_rule_coherently() -> None:
    dark_colors = dict(STANDARD_TEMPLATE["colors"])
    dark_colors |= {"statusNegative": "#ff6b6b", "rowBackground": "#20242a"}
    dark = {
        "colors": dark_colors,
        "fonts": dict(STANDARD_TEMPLATE["fonts"]),
        "rowHeight": "standard",
        "sizeStep": "md",
    }
    record = {"sessionStatus": "overdue"}
    standard_row = resolve_row_render(_plan(_rule()), record, 1)
    dark_plan = resolve_grid_render(
        ThemeLayers(org_default=STANDARD_TEMPLATE, user_choice=dark), [_rule()]
    )
    dark_row = resolve_row_render(dark_plan, record, 1)
    assert standard_row.background == STANDARD_TEMPLATE["colors"]["statusNegative"]
    assert dark_row.background == "#ff6b6b"
