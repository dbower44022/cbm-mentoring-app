"""``/theming`` — template, type-scale, and formatting-rule management (WTK-120).

The build of the WTK-114 design (``mentorapp.api.theming_surface``) for
REQ-044/045/046: the twelve ``THEMING_SURFACE`` contracts as live endpoints,
validating every write against the surface's validators — which read the
PERSISTED vocabularies in ``storage.theming``, never a local copy.

- Templates: list serves the whole live picker set (the curated launch
  templates plus the caller's own — userID bound server-side, deliberately
  not a DB-S8 grid); POST creates a USER template (``templateType`` and
  ``launchSetKey`` are server-assigned; the caller never writes them); PATCH
  is per-field + ``rowVersion``; DELETE soft-deletes the template AND its
  rules (the DB-S3 per-relationship cascade the surface declares). A system
  template refuses every write verb (``systemTemplateReadOnly``): the
  curated set is product, not user data.
- Type scale: GET serves the ONE seeded app-wide scale; PATCH retunes step
  sizes only — the write carries exactly the fixed step keys, so a step can
  never be minted or dropped through the API (REQ-046).
- Effective theme (WTK-230): ``GET /theming/effective`` serves the caller's
  boot-time resolution over REQ-044 layers one and two — the org-default
  template replaced wholesale by the stored app-wide choice — plus the
  shared type scale; the per-grid ``rowTheme`` layer stays with the grid.
- Rules: nested under their template; POST appends at the END of the
  evaluation order (the body carries no ``evaluationOrder``); reorder is its
  own PUT taking a FULL permutation, last-write-wins with no version
  round-trip (the preferences PUT precedent the surface names — order is a
  property of the list, not of one row).

The two checks the surface declares but could not wire are wired here:
duplicate template names refuse per-field against the caller's live
namespace (the partial unique indexes stay the backstop), and
``conditionField`` validates against :class:`ConditionFieldCatalog` — the
REQ-019 seam onto the grid entity catalog, fail-loud until wiring binds it
(the grids-router pattern; tests and deployments override).

Contrast never blocks (REQ-046): every USER-template save answers
``meta.contrastWarnings`` via the surface's one guardrail pass; structure
violations alone 422.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.envelope import ApiError, Envelope, field_error, ok
from mentorapp.api.errors import (
    ApiValidationError,
    RecordNotFoundError,
    StaleRowVersionError,
)
from mentorapp.api.theming_surface import (
    CODE_DUPLICATE_TEMPLATE_NAME,
    CODE_SYSTEM_TEMPLATE_READ_ONLY,
    color_slot_errors,
    font_slot_errors,
    rule_errors,
    template_contrast_warnings,
    type_step_choice_errors,
    validate_rule_order,
    validate_type_scale_write,
)
from mentorapp.observability import get_logger
from mentorapp.storage import UserPreference, utcnow
from mentorapp.storage.theming import (
    ColorTemplate,
    ConditionalFormattingRule,
    TypeScale,
    shared_type_scale,
)

# The api → ui import direction is established (theming_surface pulls the
# WTK-118 guardrail from ``ui.contrast_guardrail``); these are the layer
# vocabulary's canonical homes, never re-declared here.
from mentorapp.ui.template_flow import TEMPLATE_CHOICE_PREFERENCE_KEY
from mentorapp.ui.template_manager import FONT_WEIGHT_REGULAR
from mentorapp.ui.theming import ORG_DEFAULT_TEMPLATE_KEY, STANDARD_TEMPLATE

log = get_logger(__name__)

router = APIRouter()

# REQ-019 wired at the build (the surface's declared seam): a rule names a
# field the grid surface can actually serve, not just a non-empty string.
CODE_UNKNOWN_CONDITION_FIELD = "unknownConditionField"

_TEMPLATE_ENTITY = "colorTemplate"
_RULE_ENTITY = "conditionalFormattingRule"


class ConditionFieldCatalog(Protocol):
    """Answer whether a field name is one the consuming grids can serve (REQ-019).

    The seam onto the grid entity catalog: rules are validated like a view's
    displayed fields, and this router never owns the data-source → entity
    mapping any more than the grids router does.
    """

    def is_condition_field(self, field_name: str) -> bool:
        """True when some entity-backed data source exposes ``field_name``."""
        ...


def get_condition_field_catalog() -> ConditionFieldCatalog:
    """Provide the condition-field catalog; wiring binds it, tests override.

    Fail-loud, never an empty default: a missing binding must read as a
    deployment error, not as every rule write refusing its field.
    """
    raise RuntimeError(
        "condition field catalog provider is not wired; install theming "
        "wiring or override get_condition_field_catalog."
    )


_SessionDep = Annotated[Session, Depends(get_session)]
_UserDep = Annotated[uuid.UUID, Depends(get_current_user_id)]
_FieldCatalogDep = Annotated[ConditionFieldCatalog, Depends(get_condition_field_catalog)]


def _template_payload(template: ColorTemplate) -> dict[str, Any]:
    return {
        "colorTemplateID": template.color_template_id,
        "colorTemplateName": template.color_template_name,
        "templateType": template.template_type,
        "launchSetKey": template.launch_set_key,
        "userID": template.user_id,
        "typeScaleID": template.type_scale_id,
        "colorSlots": template.color_slots,
        "fontSlots": template.font_slots,
        "typeStepChoice": template.type_step_choice,
        "contrastGuardrailBehavior": template.contrast_guardrail_behavior,
        "rowVersion": template.row_version,
    }


def _rule_payload(rule: ConditionalFormattingRule) -> dict[str, Any]:
    return {
        "conditionalFormattingRuleID": rule.conditional_formatting_rule_id,
        "colorTemplateID": rule.color_template_id,
        "conditionField": rule.condition_field,
        "conditionOperator": rule.condition_operator,
        "conditionValue": rule.condition_value,
        "effect": rule.effect,
        "effectSlot": rule.effect_slot,
        "evaluationOrder": rule.evaluation_order,
        "rowVersion": rule.row_version,
    }


def _scale_payload(scale: TypeScale) -> dict[str, Any]:
    return {
        "typeScaleID": scale.type_scale_id,
        "typeScaleName": scale.type_scale_name,
        "scaleSteps": scale.scale_steps,
        "rowVersion": scale.row_version,
    }


def _visible_template(
    session: Session, template_id: uuid.UUID, user_id: uuid.UUID
) -> ColorTemplate:
    """The addressed live template, scoped like the list read.

    Another user's template answers the same 404 as a template that never
    existed — the ownership boundary must not be probeable by ID.
    """
    template = session.get(ColorTemplate, template_id)
    if (
        template is None
        or template.deleted_at is not None
        or template.user_id not in (None, user_id)
    ):
        raise RecordNotFoundError(_TEMPLATE_ENTITY, str(template_id))
    return template


def _refuse_system_writes(template: ColorTemplate) -> None:
    # The curated launch set is product, not user data: every write verb —
    # template edits AND its rules' create/update/delete/reorder — refuses.
    if template.user_id is None:
        raise ApiValidationError(
            [
                field_error(
                    "colorTemplateID",
                    CODE_SYSTEM_TEMPLATE_READ_ONLY,
                    "system templates are read-only; copy one into your own "
                    "templates to customize it.",
                )
            ]
        )


def _duplicate_name_errors(
    session: Session,
    user_id: uuid.UUID,
    name: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> list[ApiError]:
    """The caller's-namespace duplicate check the surface delegates to the build.

    Live USER templates of this owner only — a user template may shadow a
    system name (the split the partial unique indexes encode); the indexes
    stay the race backstop, this answer is the contract.
    """
    query = (
        select(ColorTemplate)
        .where(ColorTemplate.deleted_at.is_(None))
        .where(ColorTemplate.user_id == user_id)
        .where(ColorTemplate.color_template_name == name)
    )
    if exclude_id is not None:
        query = query.where(ColorTemplate.color_template_id != exclude_id)
    if session.scalars(query).first() is None:
        return []
    return [
        field_error(
            "colorTemplateName",
            CODE_DUPLICATE_TEMPLATE_NAME,
            f"you already have a live template named {name!r}.",
        )
    ]


def _condition_field_errors(catalog: ConditionFieldCatalog, field_name: Any) -> list[ApiError]:
    # Only judged when the surface's shape check passed — a non-string field
    # already carries its own error and must not report twice.
    if not isinstance(field_name, str) or not field_name.strip():
        return []
    if catalog.is_condition_field(field_name):
        return []
    return [
        field_error(
            "conditionField",
            CODE_UNKNOWN_CONDITION_FIELD,
            f"'{field_name}' is not a field any grid data source exposes.",
        )
    ]


def _stale_guard(current: int, submitted: int, payload: dict[str, Any]) -> None:
    if submitted != current:
        raise StaleRowVersionError(payload)


def _flush_or_conflict(
    session: Session, record_cls: type[Any], record_id: uuid.UUID, entity_type: str
) -> None:
    """Flush under the version_id_col race guard (the write-engine pattern).

    Closes the window the precheck cannot see: a write committed by another
    transaction between our read and this flush.
    """
    try:
        session.flush()
    except StaleDataError as exc:
        session.rollback()
        current = session.get(record_cls, record_id)
        if current is None or current.deleted_at is not None:
            raise RecordNotFoundError(entity_type, str(record_id)) from exc
        payload = (
            _template_payload(current)
            if isinstance(current, ColorTemplate)
            else _rule_payload(current)
            if isinstance(current, ConditionalFormattingRule)
            else _scale_payload(current)
        )
        raise StaleRowVersionError(payload) from exc


def _live_rules(session: Session, template_id: uuid.UUID) -> list[ConditionalFormattingRule]:
    return list(
        session.scalars(
            select(ConditionalFormattingRule)
            .where(ConditionalFormattingRule.deleted_at.is_(None))
            .where(ConditionalFormattingRule.color_template_id == template_id)
            .order_by(ConditionalFormattingRule.evaluation_order)
        )
    )


def _writable_rule_and_template(
    session: Session, rule_id: uuid.UUID, user_id: uuid.UUID
) -> ConditionalFormattingRule:
    rule = session.get(ConditionalFormattingRule, rule_id)
    if rule is None or rule.deleted_at is not None:
        raise RecordNotFoundError(_RULE_ENTITY, str(rule_id))
    template = _visible_template(session, rule.color_template_id, user_id)
    _refuse_system_writes(template)
    return rule


# --- Templates ---------------------------------------------------------------------


@router.get("/theming/templates")
def list_templates(session: _SessionDep, user_id: _UserDep) -> Envelope:
    """The whole live picker set: system templates plus the CALLER's own.

    Deliberately not a DB-S8 grid read (the surface's call): a template
    picker wants all of them. System templates first, then the caller's,
    each name-ordered; every record carries ``rowVersion`` (DB-S4).
    """
    templates = session.scalars(
        select(ColorTemplate)
        .where(ColorTemplate.deleted_at.is_(None))
        .where(or_(ColorTemplate.user_id.is_(None), ColorTemplate.user_id == user_id))
        .order_by(ColorTemplate.template_type, ColorTemplate.color_template_name)
    ).all()
    return ok(data=[_template_payload(t) for t in templates])


class TemplateCreateBody(BaseModel):
    """POST body: the full fixed-slot document. ``templateType`` and
    ``launchSetKey`` are server-assigned and deliberately absent."""

    model_config = ConfigDict(extra="forbid")

    color_template_name: str = Field(alias="colorTemplateName", min_length=1, max_length=200)
    color_slots: dict[str, Any] = Field(alias="colorSlots")
    font_slots: dict[str, Any] = Field(alias="fontSlots")
    type_step_choice: str = Field(alias="typeStepChoice")


@router.post("/theming/templates")
def create_template(
    body: TemplateCreateBody, session: _SessionDep, user_id: _UserDep
) -> Envelope:
    """Create a USER template over the one shared scale (REQ-044/046).

    The full fixed-slot document validates against the surface's structure
    gates plus the caller's-namespace duplicate check — every failure in one
    round trip (DB-S12). ``meta.contrastWarnings`` carries the guardrail's
    one review; warnings never refuse the save (REQ-046).
    """
    scale = shared_type_scale(session)
    steps = tuple(scale.scale_steps)
    errors = (
        color_slot_errors(body.color_slots)
        + font_slot_errors(body.font_slots, steps)
        + type_step_choice_errors(body.type_step_choice, steps)
        + _duplicate_name_errors(session, user_id, body.color_template_name)
    )
    if errors:
        raise ApiValidationError(errors)
    template = ColorTemplate(
        color_template_name=body.color_template_name,
        template_type="user",
        user_id=user_id,
        type_scale_id=scale.type_scale_id,
        color_slots=body.color_slots,
        font_slots=body.font_slots,
        type_step_choice=body.type_step_choice,
        created_by=user_id,
        modified_by=user_id,
    )
    session.add(template)
    session.commit()
    log.info(
        "user template created",
        extra={
            "context": {
                "userId": str(user_id),
                "colorTemplateID": str(template.color_template_id),
            }
        },
    )
    return ok(
        data=_template_payload(template),
        meta={"contrastWarnings": template_contrast_warnings(body.color_slots)},
    )


@router.get("/theming/templates/{template_id}")
def get_template(template_id: uuid.UUID, session: _SessionDep, user_id: _UserDep) -> Envelope:
    """One template with its live rules in evaluation order (DB-S4 read)."""
    template = _visible_template(session, template_id, user_id)
    return ok(
        data={
            **_template_payload(template),
            "rules": [_rule_payload(r) for r in _live_rules(session, template_id)],
        }
    )


class TemplatePatchBody(BaseModel):
    """PATCH body: only the changed fields plus the mandatory ``rowVersion``.

    Slot documents replace WHOLE (slots travel together, REQ-044) — there is
    no per-slot merge, so a present document always re-validates completely.
    """

    model_config = ConfigDict(extra="forbid")

    row_version: int = Field(alias="rowVersion")
    color_template_name: str | None = Field(
        default=None, alias="colorTemplateName", min_length=1, max_length=200
    )
    color_slots: dict[str, Any] | None = Field(default=None, alias="colorSlots")
    font_slots: dict[str, Any] | None = Field(default=None, alias="fontSlots")
    type_step_choice: str | None = Field(default=None, alias="typeStepChoice")


@router.patch("/theming/templates/{template_id}")
def patch_template(
    template_id: uuid.UUID,
    body: TemplatePatchBody,
    session: _SessionDep,
    user_id: _UserDep,
) -> Envelope:
    """Per-field template edit under the write contract (DB-S12, DB-S4).

    Stale ``rowVersion`` → 409 with the current record; system templates
    refuse (422); present slot documents validate whole against the LINKED
    scale. Every save answers ``meta.contrastWarnings`` for the document as
    it now stands — one guardrail review per save, never a refusal.
    """
    template = _visible_template(session, template_id, user_id)
    _refuse_system_writes(template)
    _stale_guard(template.row_version, body.row_version, _template_payload(template))
    steps = tuple(template.type_scale.scale_steps)
    sent = body.model_fields_set
    errors: list[ApiError] = []
    if "color_slots" in sent and body.color_slots is not None:
        errors += color_slot_errors(body.color_slots)
    if "font_slots" in sent and body.font_slots is not None:
        errors += font_slot_errors(body.font_slots, steps)
    if "type_step_choice" in sent:
        errors += type_step_choice_errors(body.type_step_choice, steps)
    if "color_template_name" in sent and body.color_template_name is not None:
        errors += _duplicate_name_errors(
            session, user_id, body.color_template_name, exclude_id=template_id
        )
    if errors:
        raise ApiValidationError(errors)
    for attr in ("color_template_name", "color_slots", "font_slots", "type_step_choice"):
        if attr in sent and getattr(body, attr) is not None:
            setattr(template, attr, getattr(body, attr))
    if session.is_modified(template):
        template.modified_by = user_id
        template.modified_at = utcnow()
        _flush_or_conflict(session, ColorTemplate, template_id, _TEMPLATE_ENTITY)
        session.commit()
    return ok(
        data=_template_payload(template),
        meta={"contrastWarnings": template_contrast_warnings(template.color_slots)},
    )


@router.delete("/theming/templates/{template_id}")
def delete_template(
    template_id: uuid.UUID, session: _SessionDep, user_id: _UserDep
) -> Envelope:
    """Soft-delete a USER template AND its rules (DB-S3, declared cascade).

    A rule is meaningless without its template, so the cascade is this
    relationship's declared behavior. System templates refuse (422).
    """
    template = _visible_template(session, template_id, user_id)
    _refuse_system_writes(template)
    now = utcnow()
    rules = _live_rules(session, template_id)
    for record in (template, *rules):
        record.deleted_at = now
        record.deleted_by = user_id
        record.modified_at = now
        record.modified_by = user_id
    session.commit()
    log.info(
        "user template soft-deleted",
        extra={
            "context": {
                "userId": str(user_id),
                "colorTemplateID": str(template_id),
                "rulesDeleted": len(rules),
            }
        },
    )
    return ok(
        data={
            "colorTemplateID": template_id,
            "deleted": True,
            "rulesDeleted": len(rules),
        }
    )


# --- The shared type scale ---------------------------------------------------------


@router.get("/theming/type-scale")
def get_type_scale(session: _SessionDep, user_id: _UserDep) -> Envelope:
    """The ONE app-wide scale: every step with its px size (REQ-046).

    ``rowVersion`` included — this read leads to the retune PATCH (DB-S4).
    An absent seed is a broken deployment and surfaces as the opaque 500.
    """
    return ok(data=_scale_payload(shared_type_scale(session)))


class TypeScalePatchBody(BaseModel):
    """PATCH body: the whole ``scaleSteps`` document plus ``rowVersion`` —
    exactly the fixed step keys, so a step can never be minted or dropped."""

    model_config = ConfigDict(extra="forbid")

    row_version: int = Field(alias="rowVersion")
    scale_steps: dict[str, Any] = Field(alias="scaleSteps")


@router.patch("/theming/type-scale")
def patch_type_scale(
    body: TypeScalePatchBody, session: _SessionDep, user_id: _UserDep
) -> Envelope:
    """Retune step SIZES only (REQ-046): fixed keys, ascending positive px."""
    scale = shared_type_scale(session)
    _stale_guard(scale.row_version, body.row_version, _scale_payload(scale))
    validate_type_scale_write(body.scale_steps)
    if scale.scale_steps != body.scale_steps:
        scale.scale_steps = body.scale_steps
        scale.modified_by = user_id
        scale.modified_at = utcnow()
        _flush_or_conflict(session, TypeScale, scale.type_scale_id, "typeScale")
        session.commit()
    return ok(data=_scale_payload(scale))


# --- The caller's effective theme (WTK-230, REQ-044 layers one and two) -------------


def _org_default_template(session: Session) -> ColorTemplate | None:
    """The seeded org-default row — REQ-044 layer one, when the seed exists.

    The design marks it the way the storage model does: the SYSTEM template
    whose ``launchSetKey`` is the org-branded Standard
    (``ORG_DEFAULT_TEMPLATE_KEY``); there is no separate is-org-default flag.
    """
    return session.scalars(
        select(ColorTemplate)
        .where(ColorTemplate.deleted_at.is_(None))
        .where(ColorTemplate.user_id.is_(None))
        .where(ColorTemplate.launch_set_key == ORG_DEFAULT_TEMPLATE_KEY)
    ).first()


def _chosen_template(session: Session, user_id: uuid.UUID) -> ColorTemplate | None:
    """The live template the caller's stored layer-two choice names, if any.

    The choice rides the one preference mechanism (REQ-060): the WTK-113
    flow's ``as_preference_value`` document — ``{"templateKey": …}`` — under
    :data:`TEMPLATE_CHOICE_PREFERENCE_KEY`. The CALLER's own row only: an
    org-wide default row would restate layer one, and layer one is the
    org-default TEMPLATE, not a preference. The key is a ``colorTemplateID``
    (the stored-record picker key) or a launch-set key (the curated set's).
    Anything stale — a deleted template, another user's, an unknown key —
    resolves to ``None`` so the org default shows through instead of a 500:
    a broken choice must never cost the user a working app.
    """
    row = session.scalars(
        select(UserPreference)
        .where(UserPreference.deleted_at.is_(None))
        .where(UserPreference.preference_key == TEMPLATE_CHOICE_PREFERENCE_KEY)
        .where(UserPreference.user_id == user_id)
    ).first()
    if row is None or not isinstance(row.preference_value, dict):
        return None
    key = row.preference_value.get("templateKey")
    if not isinstance(key, str) or not key:
        return None
    try:
        template = session.get(ColorTemplate, uuid.UUID(key))
    except ValueError:
        # Not an ID: the launch-set key form ("dark") names a curated
        # system template, mirroring the picker's launch entries.
        template = session.scalars(
            select(ColorTemplate)
            .where(ColorTemplate.deleted_at.is_(None))
            .where(ColorTemplate.user_id.is_(None))
            .where(ColorTemplate.launch_set_key == key)
        ).first()
    if (
        template is None
        or template.deleted_at is not None
        or template.user_id not in (None, user_id)
    ):
        log.info(
            "stored template choice did not resolve; org default applies",
            extra={"context": {"userId": str(user_id), "templateKey": key}},
        )
        return None
    return template


def _builtin_org_default_slots() -> tuple[dict[str, Any], dict[str, Any]]:
    """``STANDARD_TEMPLATE`` rendered in the persisted slot shapes.

    No migration seeds system ``colorTemplate`` rows yet, so a fresh store
    has no org-default ROW; the shipped org-branded default is the in-code
    Standard document (``ui.theming.STANDARD_TEMPLATE``). Serving it keeps
    layer one always present (REQ-044) without inventing a new seed here —
    once a seed migration lands, the row wins and this fallback goes quiet.
    """
    step: str = STANDARD_TEMPLATE["sizeStep"]
    font_slots: dict[str, Any] = {
        slot: {"stepKey": step, "fontFamily": family, "fontWeight": FONT_WEIGHT_REGULAR}
        for slot, family in STANDARD_TEMPLATE["fonts"].items()
    }
    return dict(STANDARD_TEMPLATE["colors"]), font_slots


@router.get("/theming/effective")
def get_effective_theme(session: _SessionDep, user_id: _UserDep) -> Envelope:
    """The caller's resolved app-wide theme (WTK-230): REQ-044 layers one and two.

    The org-default template, replaced WHOLESALE by the caller's app-wide
    template choice when one is stored — a template is picked, not merged,
    so the winning template's slots are served exactly as they stand. Layer
    three (the active view's ``gridView.rowTheme``) is per grid and
    deliberately absent: it belongs to the grid render, not the app shell.

    One boot-time document: every fixed color slot, both font slots, and
    ``typeScale`` exactly as ``GET /theming/type-scale`` serves it.
    """
    scale = shared_type_scale(session)
    template = _chosen_template(session, user_id) or _org_default_template(session)
    if template is not None:
        color_slots: dict[str, Any] = dict(template.color_slots)
        font_slots: dict[str, Any] = dict(template.font_slots)
    else:
        color_slots, font_slots = _builtin_org_default_slots()
    return ok(
        data={
            "colorSlots": color_slots,
            "fontSlots": font_slots,
            "typeScale": _scale_payload(scale),
        }
    )


# --- Conditional formatting rules --------------------------------------------------


@router.get("/theming/templates/{template_id}/rules")
def list_rules(template_id: uuid.UUID, session: _SessionDep, user_id: _UserDep) -> Envelope:
    """The template's live rules — the order served IS first-match-wins (REQ-045)."""
    _visible_template(session, template_id, user_id)
    return ok(data=[_rule_payload(r) for r in _live_rules(session, template_id)])


class RuleCreateBody(BaseModel):
    """POST body: condition + effect only. ``evaluationOrder`` is deliberately
    absent (extra=forbid enforces it): creates append, reorder is its own verb."""

    model_config = ConfigDict(extra="forbid")

    condition_field: str = Field(alias="conditionField")
    condition_operator: str = Field(alias="conditionOperator")
    condition_value: Any = Field(default=None, alias="conditionValue")
    effect: str
    effect_slot: str = Field(alias="effectSlot")


def _rule_document(rule: RuleCreateBody) -> dict[str, Any]:
    return {
        "conditionField": rule.condition_field,
        "conditionOperator": rule.condition_operator,
        "conditionValue": rule.condition_value,
        "effect": rule.effect,
        "effectSlot": rule.effect_slot,
    }


@router.post("/theming/templates/{template_id}/rules")
def create_rule(
    template_id: uuid.UUID,
    body: RuleCreateBody,
    session: _SessionDep,
    user_id: _UserDep,
    catalog: _FieldCatalogDep,
) -> Envelope:
    """Append one rule at the END of the evaluation order (REQ-045).

    Operators, effects, and the status-slot effect target validate against
    the persisted vocabularies; ``conditionField`` additionally validates
    against the grid entity catalog (REQ-019). System templates refuse.
    """
    template = _visible_template(session, template_id, user_id)
    _refuse_system_writes(template)
    document = _rule_document(body)
    errors = rule_errors(document) + _condition_field_errors(catalog, body.condition_field)
    if errors:
        raise ApiValidationError(errors)
    live = _live_rules(session, template_id)
    rule = ConditionalFormattingRule(
        color_template_id=template_id,
        condition_field=body.condition_field,
        condition_operator=body.condition_operator,
        condition_value=body.condition_value,
        effect=body.effect,
        effect_slot=body.effect_slot,
        evaluation_order=(live[-1].evaluation_order + 1) if live else 1,
        created_by=user_id,
        modified_by=user_id,
    )
    session.add(rule)
    session.commit()
    return ok(data=_rule_payload(rule))


class RulePatchBody(BaseModel):
    """PATCH body: only the changed rule fields plus the mandatory ``rowVersion``."""

    model_config = ConfigDict(extra="forbid")

    row_version: int = Field(alias="rowVersion")
    condition_field: str | None = Field(default=None, alias="conditionField")
    condition_operator: str | None = Field(default=None, alias="conditionOperator")
    # Nullable AND omittable: null is the presence-operator value, absence
    # leaves the value alone — fields_set is what distinguishes them.
    condition_value: Any = Field(default=None, alias="conditionValue")
    effect: str | None = None
    effect_slot: str | None = Field(default=None, alias="effectSlot")


@router.patch("/theming/rules/{rule_id}")
def patch_rule(
    rule_id: uuid.UUID,
    body: RulePatchBody,
    session: _SessionDep,
    user_id: _UserDep,
    catalog: _FieldCatalogDep,
) -> Envelope:
    """Per-field rule edit; the MERGED document re-validates on every write
    (DB-S12) — an edit can never leave a rule the create gate would refuse."""
    rule = _writable_rule_and_template(session, rule_id, user_id)
    _stale_guard(rule.row_version, body.row_version, _rule_payload(rule))
    sent = body.model_fields_set
    merged = {
        "conditionField": (
            body.condition_field if "condition_field" in sent else rule.condition_field
        ),
        "conditionOperator": (
            body.condition_operator if "condition_operator" in sent else rule.condition_operator
        ),
        "conditionValue": (
            body.condition_value if "condition_value" in sent else rule.condition_value
        ),
        "effect": body.effect if "effect" in sent else rule.effect,
        "effectSlot": body.effect_slot if "effect_slot" in sent else rule.effect_slot,
    }
    errors = rule_errors(merged)
    if "condition_field" in sent:
        errors += _condition_field_errors(catalog, body.condition_field)
    if errors:
        raise ApiValidationError(errors)
    rule.condition_field = merged["conditionField"]
    rule.condition_operator = merged["conditionOperator"]
    rule.condition_value = merged["conditionValue"]
    rule.effect = merged["effect"]
    rule.effect_slot = merged["effectSlot"]
    if session.is_modified(rule):
        rule.modified_by = user_id
        rule.modified_at = utcnow()
        _flush_or_conflict(session, ConditionalFormattingRule, rule_id, _RULE_ENTITY)
        session.commit()
    return ok(data=_rule_payload(rule))


@router.delete("/theming/rules/{rule_id}")
def delete_rule(rule_id: uuid.UUID, session: _SessionDep, user_id: _UserDep) -> Envelope:
    """Soft-delete one rule; survivors keep their relative order — gaps are
    fine, order is relative, never arithmetic (the surface's contract)."""
    rule = _writable_rule_and_template(session, rule_id, user_id)
    now = utcnow()
    rule.deleted_at = now
    rule.deleted_by = user_id
    rule.modified_at = now
    rule.modified_by = user_id
    session.commit()
    return ok(data={"conditionalFormattingRuleID": rule_id, "deleted": True})


class RuleOrderBody(BaseModel):
    """PUT body: the FULL evaluation order — every live rule ID exactly once."""

    model_config = ConfigDict(extra="forbid")

    rule_order: list[uuid.UUID] = Field(alias="ruleOrder")


@router.put("/theming/templates/{template_id}/rules/order")
def put_rule_order(
    template_id: uuid.UUID,
    body: RuleOrderBody,
    session: _SessionDep,
    user_id: _UserDep,
) -> Envelope:
    """Replace the evaluation order with a full permutation (REQ-045).

    Whole-list by design (order is a property of the LIST) and deliberately
    last-write-wins with no version round-trip — the preferences PUT
    precedent the surface names. Answers the rules in their new order.
    """
    template = _visible_template(session, template_id, user_id)
    _refuse_system_writes(template)
    live = _live_rules(session, template_id)
    validate_rule_order(body.rule_order, [r.conditional_formatting_rule_id for r in live])
    by_id = {r.conditional_formatting_rule_id: r for r in live}
    moved = [
        (position, by_id[rule_id])
        for position, rule_id in enumerate(body.rule_order, start=1)
        if by_id[rule_id].evaluation_order != position
    ]
    # Two-phase assignment: the (colorTemplateID, evaluationOrder) unique
    # index judges each UPDATE as it lands, so a direct swap collides
    # mid-flush. Negatives never collide with live 1-based orders.
    for position, rule in moved:
        rule.evaluation_order = -position
    if moved:
        session.flush()
    for position, rule in moved:
        rule.evaluation_order = position
        rule.modified_by = user_id
        rule.modified_at = utcnow()
    session.commit()
    ordered = sorted(live, key=lambda r: r.evaluation_order)
    return ok(data=[_rule_payload(r) for r in ordered])
