"""Theming management API surface design (WTK-114): the CRUD contract for
color templates, the shared type scale, and conditional formatting rules
(REQ-044/REQ-045/REQ-046 over the WTK-111 entities).

Executable design, per the repo's no-MD rule: the contracts and validators
below ARE the specification the WTK-120 build (endpoints) rides — exactly as
``grid_surface`` (WTK-042) specified the WTK-047 grid routers. Everything
validates against the PERSISTED vocabularies in ``storage.theming`` — the one
canonical home for the fixed slot/step/operator structure — never a local
copy.

The surface is twelve endpoint contracts (:data:`THEMING_SURFACE`), all
inline (none crosses the DB-S11 ten-second line — theming documents are a
handful of rows):

- **Templates** — list answers the WHOLE live set (the curated launch four
  plus the caller's own; a picker wants all of them, so this is deliberately
  not a DB-S8 grid read), POST creates a USER template (system templates are
  seeded, never API-created; ``templateType``/``launchSetKey`` are
  server-assigned), PATCH is per-field + ``rowVersion`` (DB-S12/DB-S4),
  DELETE soft-deletes the template AND its rules (a rule is meaningless
  without its template — the DB-S3 per-relationship cascade, declared here).
  A system template refuses every write verb
  (:data:`CODE_SYSTEM_TEMPLATE_READ_ONLY`): the curated set is product,
  not user data. A same-name live template in the caller's namespace fails
  per-field (:data:`CODE_DUPLICATE_TEMPLATE_NAME`) — the partial unique
  indexes are the backstop, the API answer is the contract.
- **Type scale** — GET serves the shared app-wide scale; PATCH retunes step
  SIZES only. The step SET is fixed (:data:`TYPE_SCALE_STEPS`): a write
  carries exactly those keys, so off-scale sizes stay prohibited by
  construction (REQ-046) — nobody can mint a step through the API.
- **Rules** — nested under their template (the ordered one-to-many
  association). POST appends at the END of the evaluation order — the create
  body carries no ``evaluationOrder``; reordering is its own verb
  (:data:`RULE_REORDER`), a full permutation of the template's live rule
  IDs, because first-match-wins order is a property of the LIST, not of one
  row. Operators come from :data:`CONDITION_OPERATORS`, effects repaint
  only :data:`FORMATTING_EFFECTS` slots, and the applied color is an
  ``effectSlot`` naming a :data:`STATUS_COLOR_SLOTS` slot of the owning
  template — REQ-045: effects name status slots, never literal colors
  (FND-906), so there is no hex validation anywhere on the rule surface.

Effective-theme resolution is NOT an endpoint concern and carries no numeric
precedence (FND-907, REQ-044/REQ-018): the one resolver is
``ui.theming.resolve_effective_grid_theme`` over exactly three fixed
positional layers — the org-default template, the user's app-wide template
choice, and the ACTIVE VIEW's ``gridView.rowTheme``. Nothing here reads or
writes a per-grid override row or a precedence number; templates are picked,
and the view's row theme overlays row scope only.

The contrast guardrail (REQ-046) rides create/update of USER templates:
:func:`template_contrast_warnings` delegates to the WTK-118 guardrail pass
(``ui.contrast_guardrail`` — the WTK-112 WCAG math plus the WTK-207
banding-subtlety check, one review per save) and answers its wire-shaped,
preview-carrying entries for ``meta.contrastWarnings`` — warnings, never
refusals (``ui.theming.GUARDRAIL_NEVER_BLOCKS``); structure violations alone
block.

Two checks are declared here but need the build's seams, so they are
WTK-120's to wire: ``conditionField`` must be a field the consuming view's
data source exposes (REQ-019 — needs the grid entity catalog), and the
duplicate-name refusal needs the caller's namespace query.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Final

from mentorapp.api.envelope import ApiError, field_error
from mentorapp.api.errors import ApiValidationError
from mentorapp.api.grid_surface import EndpointContract
from mentorapp.storage.theming import (
    COLOR_SLOTS,
    CONDITION_OPERATORS,
    FONT_SLOTS,
    FORMATTING_EFFECTS,
    PRESENCE_OPERATORS,
    STATUS_COLOR_SLOTS,
    TYPE_SCALE_STEPS,
)
from mentorapp.ui.contrast_guardrail import guardrail_warning_entries, run_template_guardrail
from mentorapp.ui.theming import CONTRAST_CHECKED_PAIRS

CODE_MISSING_SLOT = "missingSlot"
CODE_UNKNOWN_SLOT = "unknownSlot"
CODE_INVALID_COLOR = "invalidColor"
CODE_OFF_SCALE_STEP = "offScaleStep"
CODE_INVALID_FONT_SPEC = "invalidFontSpec"
CODE_MISSING_STEP = "missingStep"
CODE_UNKNOWN_STEP = "unknownStep"
CODE_INVALID_STEP_SIZE = "invalidStepSize"
CODE_STEPS_NOT_ASCENDING = "stepsNotAscending"
CODE_UNKNOWN_OPERATOR = "unknownOperator"
CODE_CONDITION_VALUE_REQUIRED = "conditionValueRequired"
CODE_CONDITION_VALUE_NOT_ALLOWED = "conditionValueNotAllowed"
CODE_INVALID_CONDITION_VALUE = "invalidConditionValue"
CODE_MISSING_CONDITION_FIELD = "missingConditionField"
CODE_UNKNOWN_EFFECT = "unknownEffect"
CODE_INVALID_RULE_ORDER = "invalidRuleOrder"
CODE_DUPLICATE_TEMPLATE_NAME = "duplicateTemplateName"
CODE_SYSTEM_TEMPLATE_READ_ONLY = "systemTemplateReadOnly"


# --- The endpoint contracts -------------------------------------------------------

TEMPLATE_LIST: Final = EndpointContract(
    "GET",
    "/theming/templates",
    "The whole live set — the curated launch four plus the caller's own "
    "templates (userID bound server-side); a picker list, not a DB-S8 grid.",
    over_ten_seconds=False,
)
TEMPLATE_CREATE: Final = EndpointContract(
    "POST",
    "/theming/templates",
    "Create a USER template: full fixed-slot document; contrast warnings in "
    "meta.contrastWarnings, never a refusal (REQ-046).",
    over_ten_seconds=False,
)
TEMPLATE_READ: Final = EndpointContract(
    "GET",
    "/theming/templates/{colorTemplateID}",
    "One template with its rules in evaluation order, rowVersion included "
    "(the read that can lead to an edit, DB-S4).",
    over_ten_seconds=False,
)
TEMPLATE_UPDATE: Final = EndpointContract(
    "PATCH",
    "/theming/templates/{colorTemplateID}",
    "Per-field template edit + rowVersion; slot documents replace whole "
    "(slots travel together, REQ-044); system templates refuse (422).",
    over_ten_seconds=False,
)
TEMPLATE_DELETE: Final = EndpointContract(
    "DELETE",
    "/theming/templates/{colorTemplateID}",
    "Soft-delete a USER template and its rules (declared cascade, DB-S3); "
    "system templates refuse (422).",
    over_ten_seconds=False,
)
TYPE_SCALE_READ: Final = EndpointContract(
    "GET",
    "/theming/type-scale",
    "The ONE shared app-wide scale: every step with its px size, rowVersion "
    "included (REQ-046).",
    over_ten_seconds=False,
)
TYPE_SCALE_UPDATE: Final = EndpointContract(
    "PATCH",
    "/theming/type-scale",
    "Retune step SIZES only + rowVersion: exactly the fixed step keys — the "
    "step set itself is not writable (off-scale prohibition, REQ-046).",
    over_ten_seconds=False,
)
RULE_LIST: Final = EndpointContract(
    "GET",
    "/theming/templates/{colorTemplateID}/rules",
    "The template's live rules in evaluation order — the order served IS the "
    "first-match-wins order (REQ-045).",
    over_ten_seconds=False,
)
RULE_CREATE: Final = EndpointContract(
    "POST",
    "/theming/templates/{colorTemplateID}/rules",
    "Append one rule at the END of the evaluation order; the body carries no "
    "evaluationOrder — order changes go through the reorder verb.",
    over_ten_seconds=False,
)
RULE_UPDATE: Final = EndpointContract(
    "PATCH",
    "/theming/rules/{conditionalFormattingRuleID}",
    "Per-field rule edit + rowVersion; condition/effect vocabulary "
    "re-validated on every write (DB-S12).",
    over_ten_seconds=False,
)
RULE_DELETE: Final = EndpointContract(
    "DELETE",
    "/theming/rules/{conditionalFormattingRuleID}",
    "Soft-delete one rule; surviving order values keep their relative order "
    "— gaps are fine, order is relative, never arithmetic.",
    over_ten_seconds=False,
)
RULE_REORDER: Final = EndpointContract(
    "PUT",
    "/theming/templates/{colorTemplateID}/rules/order",
    "Replace the evaluation order with a FULL permutation of the template's "
    "live rule IDs — order is a property of the list, so the write is whole-"
    "list by design (the preferences PUT precedent).",
    over_ten_seconds=False,
)

THEMING_SURFACE: Final = (
    TEMPLATE_LIST,
    TEMPLATE_CREATE,
    TEMPLATE_READ,
    TEMPLATE_UPDATE,
    TEMPLATE_DELETE,
    TYPE_SCALE_READ,
    TYPE_SCALE_UPDATE,
    RULE_LIST,
    RULE_CREATE,
    RULE_UPDATE,
    RULE_DELETE,
    RULE_REORDER,
)


# --- Fixed-slot validation (REQ-044 over the persisted structure) ------------------

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

# CSS-weight bounds: the wire carries a numeric weight, and anything outside
# the standard 100-900 range is a typo, not a styling choice.
_MIN_FONT_WEIGHT: Final = 100
_MAX_FONT_WEIGHT: Final = 900

# The parts of a font-slot spec (storage.theming: fontSlots fills FONT_SLOTS
# with {stepKey, fontFamily, fontWeight}).
_FONT_SPEC_KEYS: Final = frozenset({"stepKey", "fontFamily", "fontWeight"})


def _exact_key_errors(
    document: Mapping[str, Any],
    required: Sequence[str],
    field_prefix: str,
    *,
    missing_code: str,
    unknown_code: str,
    noun: str,
) -> list[ApiError]:
    """Exactly-these-keys errors — the shared shape of every fixed structure here."""
    errors = [
        field_error(f"{field_prefix}.{key}", missing_code, f"{noun} '{key}' is required.")
        for key in required
        if key not in document
    ]
    errors += [
        field_error(
            f"{field_prefix}.{key}",
            unknown_code,
            f"'{key}' is not a {noun}; allowed: {sorted(required)}.",
        )
        for key in sorted(set(document) - set(required))
    ]
    return errors


def color_slot_errors(color_slots: Mapping[str, Any]) -> list[ApiError]:
    """Every failure in a ``colorSlots`` document: exactly the fixed slots,
    each a ``#rrggbb`` color — accumulated, never first-failure-only (DB-S12).
    """
    errors = _exact_key_errors(
        color_slots,
        COLOR_SLOTS,
        "colorSlots",
        missing_code=CODE_MISSING_SLOT,
        unknown_code=CODE_UNKNOWN_SLOT,
        noun="color slot",
    )
    for slot in COLOR_SLOTS:
        value = color_slots.get(slot)
        if slot in color_slots and (
            not isinstance(value, str) or _HEX_COLOR.match(value) is None
        ):
            errors.append(
                field_error(
                    f"colorSlots.{slot}",
                    CODE_INVALID_COLOR,
                    f"slot '{slot}' must be a #rrggbb color, got {value!r}.",
                )
            )
    return errors


def _font_spec_errors(
    slot: str, spec: Mapping[str, Any], steps: Sequence[str]
) -> list[ApiError]:
    prefix = f"fontSlots.{slot}"
    errors = _exact_key_errors(
        spec,
        sorted(_FONT_SPEC_KEYS),
        prefix,
        missing_code=CODE_INVALID_FONT_SPEC,
        unknown_code=CODE_INVALID_FONT_SPEC,
        noun="font-spec key",
    )
    if "stepKey" in spec and spec["stepKey"] not in steps:
        errors.append(
            field_error(
                f"{prefix}.stepKey",
                CODE_OFF_SCALE_STEP,
                f"'{spec['stepKey']}' is not a step of the template's type "
                f"scale; steps: {sorted(steps)}.",
            )
        )
    family = spec.get("fontFamily")
    if "fontFamily" in spec and (not isinstance(family, str) or not family.strip()):
        errors.append(
            field_error(
                f"{prefix}.fontFamily",
                CODE_INVALID_FONT_SPEC,
                "fontFamily must be a non-empty font family name.",
            )
        )
    weight = spec.get("fontWeight")
    if "fontWeight" in spec and (
        not isinstance(weight, int)
        or isinstance(weight, bool)
        or not _MIN_FONT_WEIGHT <= weight <= _MAX_FONT_WEIGHT
    ):
        errors.append(
            field_error(
                f"{prefix}.fontWeight",
                CODE_INVALID_FONT_SPEC,
                f"fontWeight must be an integer weight "
                f"{_MIN_FONT_WEIGHT}-{_MAX_FONT_WEIGHT}, got {weight!r}.",
            )
        )
    return errors


def font_slot_errors(
    font_slots: Mapping[str, Any], scale_steps: Sequence[str]
) -> list[ApiError]:
    """Every failure in a ``fontSlots`` document: exactly the fixed font slots,
    each a ``{stepKey, fontFamily, fontWeight}`` spec whose step names a step
    of the template's LINKED scale (the WTK-111 association constraint).
    """
    errors = _exact_key_errors(
        font_slots,
        FONT_SLOTS,
        "fontSlots",
        missing_code=CODE_MISSING_SLOT,
        unknown_code=CODE_UNKNOWN_SLOT,
        noun="font slot",
    )
    for slot in FONT_SLOTS:
        spec = font_slots.get(slot)
        if slot not in font_slots:
            continue
        if not isinstance(spec, Mapping):
            errors.append(
                field_error(
                    f"fontSlots.{slot}",
                    CODE_INVALID_FONT_SPEC,
                    f"slot '{slot}' must be a {{stepKey, fontFamily, fontWeight}} spec.",
                )
            )
            continue
        errors += _font_spec_errors(slot, spec, scale_steps)
    return errors


def type_step_choice_errors(step: Any, scale_steps: Sequence[str]) -> list[ApiError]:
    """``typeStepChoice`` must name a step of the template's linked scale."""
    if step in scale_steps:
        return []
    return [
        field_error(
            "typeStepChoice",
            CODE_OFF_SCALE_STEP,
            f"typeStepChoice must name a type-scale step {sorted(scale_steps)}, got {step!r}.",
        )
    ]


def scale_step_errors(scale_steps: Mapping[str, Any]) -> list[ApiError]:
    """Every failure in a ``scaleSteps`` document: exactly the fixed step set,
    positive integer px sizes, strictly ascending xs→xl — the steps are
    size-ordered by definition, so a scale where lg is smaller than sm is a
    data error, not a typography choice.
    """
    errors = _exact_key_errors(
        scale_steps,
        TYPE_SCALE_STEPS,
        "scaleSteps",
        missing_code=CODE_MISSING_STEP,
        unknown_code=CODE_UNKNOWN_STEP,
        noun="type-scale step",
    )
    sizes: list[int] = []
    for step in TYPE_SCALE_STEPS:
        size = scale_steps.get(step)
        if step not in scale_steps:
            continue
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            errors.append(
                field_error(
                    f"scaleSteps.{step}",
                    CODE_INVALID_STEP_SIZE,
                    f"step '{step}' must be a positive integer px size, got {size!r}.",
                )
            )
            continue
        sizes.append(size)
    if len(sizes) == len(TYPE_SCALE_STEPS) and sizes != sorted(set(sizes)):
        errors.append(
            field_error(
                "scaleSteps",
                CODE_STEPS_NOT_ASCENDING,
                f"step sizes must strictly ascend {' < '.join(TYPE_SCALE_STEPS)}.",
            )
        )
    return errors


# --- Standard-operator rule validation (REQ-045) -----------------------------------


def _condition_value_errors(operator: str, value: Any) -> list[ApiError]:
    if operator in PRESENCE_OPERATORS:
        if value is None:
            return []
        return [
            field_error(
                "conditionValue",
                CODE_CONDITION_VALUE_NOT_ALLOWED,
                f"'{operator}' tests the field itself; conditionValue must be null.",
            )
        ]
    if value is None:
        return [
            field_error(
                "conditionValue",
                CODE_CONDITION_VALUE_REQUIRED,
                f"'{operator}' compares against a value; conditionValue is required.",
            )
        ]
    # Comparison values are JSON scalars — a dict/list comparand has no
    # defined ordering or equality on the wire and is always a client bug.
    if operator == "contains" and not isinstance(value, str):
        return [
            field_error(
                "conditionValue",
                CODE_INVALID_CONDITION_VALUE,
                f"'contains' takes a string, got {value!r}.",
            )
        ]
    if operator in ("greaterThan", "lessThan") and (
        isinstance(value, bool) or not isinstance(value, (int, float, str))
    ):
        return [
            field_error(
                "conditionValue",
                CODE_INVALID_CONDITION_VALUE,
                f"'{operator}' takes a number or string, got {value!r}.",
            )
        ]
    if operator in ("equals", "notEquals") and not isinstance(value, (str, int, float, bool)):
        return [
            field_error(
                "conditionValue",
                CODE_INVALID_CONDITION_VALUE,
                f"'{operator}' takes a scalar, got {value!r}.",
            )
        ]
    return []


def rule_errors(rule: Mapping[str, Any]) -> list[ApiError]:
    """Every failure in one rule's condition + effect, accumulated (DB-S12).

    Wire keys: ``conditionField``, ``conditionOperator``, ``conditionValue``,
    ``effect``, ``effectSlot``. Whether ``conditionField`` actually exists on
    the consuming view's data source is the REQ-019 check the WTK-120 build
    wires through the grid entity catalog — here it must only be a non-empty
    field name.
    """
    errors: list[ApiError] = []
    condition_field = rule.get("conditionField")
    if not isinstance(condition_field, str) or not condition_field.strip():
        errors.append(
            field_error(
                "conditionField",
                CODE_MISSING_CONDITION_FIELD,
                "conditionField must name a field of the consuming data source.",
            )
        )
    operator = rule.get("conditionOperator")
    if operator not in CONDITION_OPERATORS:
        errors.append(
            field_error(
                "conditionOperator",
                CODE_UNKNOWN_OPERATOR,
                f"conditionOperator must be one of {sorted(CONDITION_OPERATORS)}, "
                f"got {operator!r}.",
            )
        )
    else:
        errors += _condition_value_errors(operator, rule.get("conditionValue"))
    effect = rule.get("effect")
    if effect not in FORMATTING_EFFECTS:
        errors.append(
            field_error(
                "effect",
                CODE_UNKNOWN_EFFECT,
                f"effect must repaint a fixed slot {sorted(FORMATTING_EFFECTS)}, "
                f"got {effect!r}.",
            )
        )
    # REQ-045: effects name status slots, never literal colors (FND-906) —
    # an unknown name AND a #rrggbb literal refuse under the same code.
    effect_slot = rule.get("effectSlot")
    if effect_slot not in STATUS_COLOR_SLOTS:
        errors.append(
            field_error(
                "effectSlot",
                CODE_UNKNOWN_SLOT,
                f"effectSlot must name a status color slot "
                f"{sorted(STATUS_COLOR_SLOTS)}, never a literal color; "
                f"got {effect_slot!r}.",
            )
        )
    return errors


def rule_order_errors(
    ordered_ids: Sequence[uuid.UUID], live_ids: Sequence[uuid.UUID]
) -> list[ApiError]:
    """The reorder write must be a FULL permutation of the template's live rules.

    A partial list would silently decide the fate of unnamed rules; a foreign
    or duplicated ID would corrupt first-match-wins. All three refuse under
    one code — the client's fix is the same: resend the whole current list.
    """
    submitted, live = list(ordered_ids), set(live_ids)
    problems = []
    if len(submitted) != len(set(submitted)):
        problems.append("duplicate rule IDs")
    if set(submitted) - live:
        problems.append("IDs that are not this template's live rules")
    if live - set(submitted):
        problems.append("fewer IDs than the template's live rules")
    if not problems:
        return []
    return [
        field_error(
            "ruleOrder",
            CODE_INVALID_RULE_ORDER,
            "the order must list every live rule of this template exactly "
            f"once; this request has {', '.join(problems)}.",
        )
    ]


# --- The write-verb validators the build calls (raise = 422, all at once) ----------


def validate_template_write(
    *,
    color_slots: Mapping[str, Any],
    font_slots: Mapping[str, Any],
    type_step_choice: Any,
    scale_steps: Sequence[str],
) -> None:
    """Structure gate for a template create/update: the full fixed-slot
    document against the LINKED scale's steps. Raises with every failure in
    one round trip; readability is not judged here (the guardrail warns,
    never blocks).
    """
    errors = (
        color_slot_errors(color_slots)
        + font_slot_errors(font_slots, scale_steps)
        + type_step_choice_errors(type_step_choice, scale_steps)
    )
    if errors:
        raise ApiValidationError(errors)


def validate_type_scale_write(scale_steps: Mapping[str, Any]) -> None:
    """Structure gate for the type-scale PATCH: exactly the fixed steps,
    ascending positive sizes. Raises with every failure in one round trip."""
    errors = scale_step_errors(scale_steps)
    if errors:
        raise ApiValidationError(errors)


def validate_rule_write(rule: Mapping[str, Any]) -> None:
    """Structure gate for a rule create/update. Raises with every failure in
    one round trip."""
    errors = rule_errors(rule)
    if errors:
        raise ApiValidationError(errors)


def validate_rule_order(
    ordered_ids: Sequence[uuid.UUID], live_ids: Sequence[uuid.UUID]
) -> None:
    """Gate for the reorder PUT: a full permutation or a 422."""
    errors = rule_order_errors(ordered_ids, live_ids)
    if errors:
        raise ApiValidationError(errors)


# --- The contrast guardrail on the management surface (REQ-046) --------------------

# The persisted structure now carries the full UI slot vocabulary (FND-905),
# so the checked pairs ARE the WTK-112 originals — one canonical home, and
# the math and the minimum are the ui.theming originals, never re-derived.
TEMPLATE_CONTRAST_PAIRS: Final[tuple[tuple[str, str], ...]] = CONTRAST_CHECKED_PAIRS


def template_contrast_warnings(color_slots: Mapping[str, str]) -> list[dict[str, Any]]:
    """The ``meta.contrastWarnings`` entries for one USER-template save.

    Delegates to the WTK-118 guardrail pass, so one save gets ONE review —
    readability warnings over the WTK-112 pairs plus the WTK-207
    banding-subtlety check — in the preview-carrying wire shape
    (:func:`~mentorapp.ui.contrast_guardrail.guardrail_warning_entries`).
    Educate voice, and NEVER an error: the save verb proceeds regardless
    (REQ-046; system templates ship curated and skip this pass). Call only
    after :func:`validate_template_write` — a malformed document is a
    contract violation, not a styling choice.
    """
    return guardrail_warning_entries(run_template_guardrail(color_slots))
