"""The theming management API surface design (WTK-114): REQ-044/045/046.

What the WTK-120 build relies on, proven here at design level:

- The twelve endpoint contracts live under ``/theming``, none crosses the
  DB-S11 ten-second line, and reordering is its own whole-list verb.
- Fixed-slot enforcement: a template document carries exactly the persisted
  ``COLOR_SLOTS``/``FONT_SLOTS`` structure, font steps and the base step
  name steps of the LINKED scale, and every failure of a write reports in
  ONE round trip (DB-S12).
- The type-scale write carries exactly the fixed step set — the off-scale
  prohibition (REQ-046) means step sizes retune but steps are never minted.
- Standard-operator enforcement: rule conditions come from
  ``CONDITION_OPERATORS`` with per-operator value shapes, effects repaint
  only ``FORMATTING_EFFECTS`` slots (REQ-045).
- The contrast guardrail WARNS with wire-shaped entries and never raises —
  readability is the user's call (REQ-046).
"""

from __future__ import annotations

import uuid

import pytest

from mentorapp.api import (
    THEMING_SURFACE,
    ApiValidationError,
    color_slot_errors,
    font_slot_errors,
    rule_order_errors,
    scale_step_errors,
    template_contrast_warnings,
    type_step_choice_errors,
    validate_rule_order,
    validate_rule_write,
    validate_template_write,
    validate_type_scale_write,
)
from mentorapp.api.theming_surface import (
    CODE_CONDITION_VALUE_NOT_ALLOWED,
    CODE_CONDITION_VALUE_REQUIRED,
    CODE_INVALID_COLOR,
    CODE_INVALID_CONDITION_VALUE,
    CODE_INVALID_FONT_SPEC,
    CODE_INVALID_RULE_ORDER,
    CODE_INVALID_STEP_SIZE,
    CODE_MISSING_CONDITION_FIELD,
    CODE_MISSING_SLOT,
    CODE_MISSING_STEP,
    CODE_OFF_SCALE_STEP,
    CODE_STEPS_NOT_ASCENDING,
    CODE_UNKNOWN_EFFECT,
    CODE_UNKNOWN_OPERATOR,
    CODE_UNKNOWN_SLOT,
    CODE_UNKNOWN_STEP,
    PRESENCE_OPERATORS,
    RULE_CREATE,
    RULE_REORDER,
    TEMPLATE_CREATE,
    TEMPLATE_UPDATE,
    TYPE_SCALE_UPDATE,
)
from mentorapp.storage.theming import (
    COLOR_SLOTS,
    CONDITION_OPERATORS,
    TYPE_SCALE_STEPS,
)
from mentorapp.ui.theming import CONTRAST_MINIMUM

STEPS = list(TYPE_SCALE_STEPS)


def readable_colors() -> dict[str, str]:
    """A complete colorSlots document with comfortably readable pairs."""
    return {
        "rowBackground": "#ffffff",
        "alternateRowBackground": "#f0f4f8",
        "rowText": "#1a1a1a",
        "selectedRowBackground": "#d6e6f2",
        "selectedRowText": "#102a43",
        "accent": "#2a6f97",
    }


def fonts() -> dict[str, dict[str, object]]:
    return {
        "rowFont": {"stepKey": "md", "fontFamily": "Inter", "fontWeight": 400},
        "headerFont": {"stepKey": "sm", "fontFamily": "Inter", "fontWeight": 600},
    }


def rule() -> dict[str, object]:
    return {
        "conditionField": "engagementStatus",
        "conditionOperator": "equals",
        "conditionValue": "overdue",
        "effect": "rowBackground",
        "effectColor": "#ffe0e0",
    }


def codes(errors: list[dict[str, object]]) -> set[tuple[str | None, str]]:
    return {(entry["fieldName"], entry["code"]) for entry in errors}


# --- The endpoint contracts ---------------------------------------------------------


def test_surface_is_twelve_inline_contracts_under_theming() -> None:
    assert len(THEMING_SURFACE) == 12
    for contract in THEMING_SURFACE:
        assert contract.path.startswith("/theming")
        # Theming documents are a handful of rows; nothing here may enqueue.
        assert contract.over_ten_seconds is False


def test_write_verbs_follow_the_write_contract() -> None:
    # PATCH is the primary write verb (DB-S12); reorder is the one deliberate
    # whole-list PUT because first-match-wins order belongs to the list.
    assert TEMPLATE_UPDATE.method == "PATCH"
    assert TYPE_SCALE_UPDATE.method == "PATCH"
    assert RULE_REORDER.method == "PUT"
    assert RULE_REORDER.path.endswith("/rules/order")
    # Rules are created under their template — the ordered association.
    assert RULE_CREATE.path == "/theming/templates/{colorTemplateID}/rules"
    assert TEMPLATE_CREATE.method == "POST"


# --- Fixed color slots (REQ-044) ----------------------------------------------------


def test_complete_color_document_passes() -> None:
    assert color_slot_errors(readable_colors()) == []


def test_color_failures_accumulate_in_one_pass() -> None:
    document = readable_colors()
    del document["accent"]
    document["chromeHeader"] = "#ffffff"  # not a persisted slot
    document["rowText"] = "not-a-color"
    found = codes(color_slot_errors(document))
    assert ("colorSlots.accent", CODE_MISSING_SLOT) in found
    assert ("colorSlots.chromeHeader", CODE_UNKNOWN_SLOT) in found
    assert ("colorSlots.rowText", CODE_INVALID_COLOR) in found


# --- Fixed font slots against the linked scale (REQ-044/REQ-046) --------------------


def test_complete_font_document_passes() -> None:
    assert font_slot_errors(fonts(), STEPS) == []


def test_font_step_must_belong_to_the_linked_scale() -> None:
    document = fonts()
    document["rowFont"]["stepKey"] = "huge"
    found = codes(font_slot_errors(document, STEPS))
    assert ("fontSlots.rowFont.stepKey", CODE_OFF_SCALE_STEP) in found


def test_font_spec_shape_is_enforced_per_part() -> None:
    document = {
        "rowFont": {"stepKey": "md", "fontFamily": "", "fontWeight": 950},
        "headerFont": "Inter",  # not a spec at all
    }
    found = codes(font_slot_errors(document, STEPS))
    assert ("fontSlots.rowFont.fontFamily", CODE_INVALID_FONT_SPEC) in found
    assert ("fontSlots.rowFont.fontWeight", CODE_INVALID_FONT_SPEC) in found
    assert ("fontSlots.headerFont", CODE_INVALID_FONT_SPEC) in found


def test_missing_and_unknown_font_slots_reported() -> None:
    found = codes(font_slot_errors({"bodyFont": fonts()["rowFont"]}, STEPS))
    assert ("fontSlots.rowFont", CODE_MISSING_SLOT) in found
    assert ("fontSlots.headerFont", CODE_MISSING_SLOT) in found
    assert ("fontSlots.bodyFont", CODE_UNKNOWN_SLOT) in found


def test_type_step_choice_is_scale_bound() -> None:
    assert type_step_choice_errors("md", STEPS) == []
    found = codes(type_step_choice_errors("giant", STEPS))
    assert ("typeStepChoice", CODE_OFF_SCALE_STEP) in found


def test_template_write_gate_reports_everything_at_once() -> None:
    colors = readable_colors()
    del colors["accent"]
    document = fonts()
    document["rowFont"]["stepKey"] = "huge"
    with pytest.raises(ApiValidationError) as failure:
        validate_template_write(
            color_slots=colors,
            font_slots=document,
            type_step_choice="giant",
            scale_steps=STEPS,
        )
    found = codes(failure.value.errors)
    assert ("colorSlots.accent", CODE_MISSING_SLOT) in found
    assert ("fontSlots.rowFont.stepKey", CODE_OFF_SCALE_STEP) in found
    assert ("typeStepChoice", CODE_OFF_SCALE_STEP) in found


def test_valid_template_write_passes() -> None:
    validate_template_write(
        color_slots=readable_colors(),
        font_slots=fonts(),
        type_step_choice="md",
        scale_steps=STEPS,
    )


# --- The shared type scale: fixed step set, retunable sizes (REQ-046) ---------------


def test_valid_scale_passes() -> None:
    assert scale_step_errors({"xs": 11, "sm": 12, "md": 14, "lg": 16, "xl": 20}) == []


def test_steps_are_never_minted_or_dropped() -> None:
    found = codes(scale_step_errors({"xs": 11, "sm": 12, "md": 14, "lg": 16, "xxl": 28}))
    assert ("scaleSteps.xl", CODE_MISSING_STEP) in found
    assert ("scaleSteps.xxl", CODE_UNKNOWN_STEP) in found


def test_step_sizes_must_be_positive_integers() -> None:
    found = codes(scale_step_errors({"xs": 0, "sm": 12.5, "md": True, "lg": "16", "xl": 20}))
    assert ("scaleSteps.xs", CODE_INVALID_STEP_SIZE) in found
    assert ("scaleSteps.sm", CODE_INVALID_STEP_SIZE) in found
    assert ("scaleSteps.md", CODE_INVALID_STEP_SIZE) in found
    assert ("scaleSteps.lg", CODE_INVALID_STEP_SIZE) in found


def test_step_sizes_must_strictly_ascend() -> None:
    found = codes(scale_step_errors({"xs": 11, "sm": 14, "md": 12, "lg": 16, "xl": 20}))
    assert ("scaleSteps", CODE_STEPS_NOT_ASCENDING) in found
    with pytest.raises(ApiValidationError):
        validate_type_scale_write({"xs": 11, "sm": 11, "md": 14, "lg": 16, "xl": 20})


# --- Standard operators and slot-limited effects (REQ-045) --------------------------


def test_valid_rule_passes_for_every_operator() -> None:
    values: dict[str, object] = {
        "equals": "overdue",
        "notEquals": 3,
        "greaterThan": 10,
        "lessThan": "2026-01-01",
        "contains": "mentor",
        "isEmpty": None,
        "isNotEmpty": None,
    }
    assert set(values) == set(CONDITION_OPERATORS)
    for operator, value in values.items():
        document = rule() | {"conditionOperator": operator, "conditionValue": value}
        validate_rule_write(document)


def test_unknown_operator_and_effect_refused() -> None:
    document = rule() | {"conditionOperator": "matchesRegex", "effect": "blinkingText"}
    with pytest.raises(ApiValidationError) as failure:
        validate_rule_write(document)
    found = codes(failure.value.errors)
    assert ("conditionOperator", CODE_UNKNOWN_OPERATOR) in found
    assert ("effect", CODE_UNKNOWN_EFFECT) in found


def test_presence_operators_forbid_a_value() -> None:
    for operator in PRESENCE_OPERATORS:
        document = rule() | {"conditionOperator": operator, "conditionValue": "x"}
        with pytest.raises(ApiValidationError) as failure:
            validate_rule_write(document)
        assert ("conditionValue", CODE_CONDITION_VALUE_NOT_ALLOWED) in codes(
            failure.value.errors
        )


def test_comparison_operators_require_a_shaped_value() -> None:
    cases: list[tuple[str, object, str]] = [
        ("equals", None, CODE_CONDITION_VALUE_REQUIRED),
        ("equals", {"nested": 1}, CODE_INVALID_CONDITION_VALUE),
        ("contains", 5, CODE_INVALID_CONDITION_VALUE),
        ("greaterThan", True, CODE_INVALID_CONDITION_VALUE),
    ]
    for operator, value, code in cases:
        document = rule() | {"conditionOperator": operator, "conditionValue": value}
        with pytest.raises(ApiValidationError) as failure:
            validate_rule_write(document)
        assert ("conditionValue", code) in codes(failure.value.errors)


def test_rule_failures_report_in_one_round_trip() -> None:
    document = {
        "conditionField": "  ",
        "conditionOperator": "equals",
        "conditionValue": None,
        "effect": "sparkle",
        "effectColor": "red",
    }
    with pytest.raises(ApiValidationError) as failure:
        validate_rule_write(document)
    found = codes(failure.value.errors)
    assert ("conditionField", CODE_MISSING_CONDITION_FIELD) in found
    assert ("conditionValue", CODE_CONDITION_VALUE_REQUIRED) in found
    assert ("effect", CODE_UNKNOWN_EFFECT) in found
    assert ("effectColor", CODE_INVALID_COLOR) in found


# --- Reordering is a full permutation (REQ-045) --------------------------------------


def test_reorder_accepts_exactly_a_permutation() -> None:
    live = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    assert rule_order_errors(list(reversed(live)), live) == []
    validate_rule_order(list(reversed(live)), live)


def test_reorder_refuses_partial_foreign_and_duplicated_lists() -> None:
    live = [uuid.uuid4(), uuid.uuid4()]
    for submitted in (
        live[:1],  # partial: silently decides the unnamed rule's place
        [*live, uuid.uuid4()],  # foreign rule
        [live[0], live[0]],  # duplicate entry
    ):
        with pytest.raises(ApiValidationError) as failure:
            validate_rule_order(submitted, live)
        assert ("ruleOrder", CODE_INVALID_RULE_ORDER) in codes(failure.value.errors)


# --- The contrast guardrail warns, never blocks (REQ-046) ---------------------------


def test_readable_template_gets_no_warnings() -> None:
    assert template_contrast_warnings(readable_colors()) == []


def test_unreadable_pair_warns_with_the_wire_shape() -> None:
    colors = readable_colors() | {"rowText": "#c9ced4"}  # light grey on white
    warnings = template_contrast_warnings(colors)
    pairs = {(w["textSlot"], w["backgroundSlot"]) for w in warnings}
    assert ("rowText", "rowBackground") in pairs
    for warning in warnings:
        assert warning["ratio"] < CONTRAST_MINIMUM
        assert warning["minimum"] == CONTRAST_MINIMUM
        assert "never blocked" in warning["message"]


def test_guardrail_never_raises_even_when_everything_is_unreadable() -> None:
    colors = {slot: "#808080" for slot in COLOR_SLOTS}
    warnings = template_contrast_warnings(colors)
    # Every checked pair is grey-on-grey (ratio 1.0) — still only warnings.
    assert len(warnings) == 3
