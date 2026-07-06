"""Color-template, type-scale, and conditional-formatting entities (WTK-111).

Implements the look-and-feel data model (PI-007), reconciled to the UI design
(WSK-019, FND-905/906/907): ``colorTemplate`` is one curated or user-authored
theme — its fixed color/font slot structure, the launch-set membership
(Standard/Compact/Large print/Dark), its chosen type step, and the contrast
guardrail. ``typeScale`` is the shared app-wide typography scale: a defined
step set that every font size must come from — off-scale sizes are prohibited
(API-validated per DB-S7, with ORM ``@validates`` backstops at the
persistence boundary; WTK-116 seeds the ONE shared row, read via
:func:`shared_type_scale`).
``conditionalFormattingRule`` is one rule of a template's conditional
formatting (the grid standard: "conditional formatting lives in the theme"),
evaluated first-match-wins in ``evaluationOrder``; its effect paints a STATUS
slot's color, never a literal color (REQ-045, FND-906).

Associations: ``colorTemplate.typeScaleID`` is the many-to-one
colorTemplate ↔ typeScale association (the template's font slots and
``typeStepChoice`` must name steps of THAT scale);
``conditionalFormattingRule.colorTemplateID`` + ``evaluationOrder`` is the
ordered one-to-many theme ↔ rule association. There is NO per-grid override
entity and no numeric layer precedence (FND-907): the layering model is
exactly three fixed positional layers — org-default template → the user's
app-wide template choice → the ACTIVE VIEW's ``gridView.rowTheme``
(REQ-044/REQ-018) — so the row-theme association already lives on the view.

This module is the one canonical home of the persisted slot vocabulary; the
tuples below ARE the UI design's fixed slot structure (FND-905, REQ-044) and
``mentorapp.ui.theming`` re-exports them (ui imports storage here — the
repo's vocab-sharing direction, e.g. ``SELECTION_CONTRACTS`` — because the
reverse import would cycle through the ``ui`` package).

These are platform tables (``StructuralColumnsMixin`` + ``Base``, not
``BaseEntity``): like ``grid`` and ``gridView`` they are app configuration,
get no schema-registry rows and no generated read views, and never surface
through the admin read surface. Every foreign-key column carries the exact
name of the primary key it references (DB-R2b).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Final

from sqlalchemy import ForeignKey, Index, String, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship, validates

from mentorapp.storage.base import Base, JsonValue, StructuralColumnsMixin, uuid7

# Same partial live-row predicate as grids.py (DB-S3, REQ-052).
_LIVE = text('"deletedAt" IS NULL')
# Template-name uniqueness splits like gridView's: system templates share one
# namespace; a user's own templates share theirs (they may shadow a system name).
_LIVE_SYSTEM_TEMPLATE = text('"deletedAt" IS NULL AND "userID" IS NULL')
_LIVE_USER_TEMPLATE = text('"deletedAt" IS NULL AND "userID" IS NOT NULL')

# The system/user template split (PI-007: curated launch set + user-created
# templates). App-validated vocabulary, never a database enum (DB-S7).
TEMPLATE_TYPES: Final[tuple[str, ...]] = ("system", "user")

# The curated launch set (PI-007): Standard, Compact, Large print, Dark.
LAUNCH_SETS: Final[tuple[str, ...]] = ("standard", "compact", "largePrint", "dark")

# The fixed color-slot structure is the UI design's 15-slot vocabulary
# (FND-905, REQ-044): every template fills exactly these slots — a template
# never invents a slot, so every consumer (chrome, grid rows, status effects)
# reads a color that is guaranteed to exist. The three groups carry the
# scoping the UI semantics depend on.

# App-chrome color slots: shared shell surfaces a view's row theme may never
# touch (REQ-018 — a row theme's reach is its grid's rows).
CHROME_COLOR_SLOTS: Final[tuple[str, ...]] = (
    "appBackground",
    "panelBackground",
    "headerBackground",
    "headerText",
    "accent",
)

# Row-scoped color slots: the grid-row surfaces a view's row theme may
# override (the UI's exact names — ``rowAlternateBackground``, FND-905).
ROW_THEME_COLOR_SLOTS: Final[tuple[str, ...]] = (
    "rowBackground",
    "rowAlternateBackground",
    "rowText",
    "selectedRowBackground",
    "selectedRowText",
    "groupHeaderBackground",
    "groupHeaderText",
)

# Status slots: the palette conditional-formatting effects draw from —
# REQ-045: rules name these slots, never literal colors (FND-906).
STATUS_COLOR_SLOTS: Final[tuple[str, ...]] = (
    "statusPositive",
    "statusWarning",
    "statusNegative",
)

COLOR_SLOTS: Final[tuple[str, ...]] = (
    CHROME_COLOR_SLOTS + ROW_THEME_COLOR_SLOTS + STATUS_COLOR_SLOTS
)

# The fixed font-slot structure — the UI design's two slots (FND-905): each
# names a step of the template's type scale (plus family/weight); the slot
# set is as fixed as COLOR_SLOTS.
FONT_SLOTS: Final[tuple[str, ...]] = ("uiFont", "dataFont")

# The defined step set of the app-wide typography scale. A typeScale row maps
# each step to a concrete size; fonts choose steps, never raw sizes.
TYPE_SCALE_STEPS: Final[tuple[str, ...]] = ("xs", "sm", "md", "lg", "xl")

# The well-known name of the ONE persisted app-wide scale (WTK-116, REQ-046):
# migration 0011 seeds it, :func:`shared_type_scale` reads it, and the WTK-114
# type-scale surface retunes exactly this row. No create-scale endpoint
# exists, so the seed + this name IS the singleton contract.
SHARED_TYPE_SCALE_NAME: Final = "App-wide type scale"

# The design-default px size of every defined step (the UX design's scale,
# WTK-112) — what the seed persists and what a new scale row starts from.
# The one canonical home (FND-905 direction): ``ui.theming`` re-exports this
# mapping; the LIVE values are the seeded row, retunable per REQ-046.
TYPE_SCALE_DEFAULT_SIZES: Final[dict[str, int]] = {
    "xs": 11,
    "sm": 12,
    "md": 14,
    "lg": 16,
    "xl": 20,
}

# PI-007 rules the guardrail educate-style: it WARNS on unreadable slot
# combinations and never blocks a save. "warn" is the only sanctioned value;
# the column names the contract rather than hardcoding it (the keyboardModelKey
# precedent on ``grid``).
CONTRAST_GUARDRAIL_BEHAVIORS: Final[tuple[str, ...]] = ("warn",)

# Condition operators a formatting rule may use, app-validated like the rest.
CONDITION_OPERATORS: Final[tuple[str, ...]] = (
    "equals",
    "notEquals",
    "greaterThan",
    "lessThan",
    "contains",
    "isEmpty",
    "isNotEmpty",
)

# The fixed effect slots a matching rule may repaint — the effect enum is
# limited to these slots (WTK-111); a rule never invents a visual effect.
FORMATTING_EFFECTS: Final[tuple[str, ...]] = ("rowBackground", "rowText", "accent")


class TypeScale(StructuralColumnsMixin, Base):
    """The shared app-wide typography scale (PI-007).

    ``scaleSteps`` maps every ``TYPE_SCALE_STEPS`` key to its concrete size
    (px) — exactly those keys, no extras: off-scale sizes are prohibited, so
    a font can only ever name a step. API-validated, like every slot/step
    structure in this module (DB-S7).
    """

    __tablename__ = "typeScale"
    __table_args__ = (
        Index(
            "uq_typeScale_typeScaleName_live",
            "typeScaleName",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    type_scale_id: Mapped[uuid.UUID] = mapped_column(
        "typeScaleID", primary_key=True, default=uuid7
    )
    type_scale_name: Mapped[str] = mapped_column("typeScaleName", String(200), nullable=False)
    # {stepKey: sizePx} covering exactly TYPE_SCALE_STEPS.
    scale_steps: Mapped[dict[str, Any]] = mapped_column(
        "scaleSteps",
        JsonValue,
        nullable=False,
        default=lambda: dict(TYPE_SCALE_DEFAULT_SIZES),
    )

    color_templates: Mapped[list[ColorTemplate]] = relationship(back_populates="type_scale")

    @validates("scale_steps")
    def _reject_off_scale_steps(self, _key: str, value: dict[str, Any]) -> dict[str, Any]:
        # The persistence-boundary backstop of the off-scale prohibition
        # (WTK-116, REQ-046) for writers that never ride the API surface
        # (seeds, jobs, tests); the WTK-114 validators stay the field-shaped
        # user-facing home. Step-set exactness only — size shape/order rules
        # live there, not here.
        off_scale = sorted(set(value) - set(TYPE_SCALE_STEPS))
        missing = sorted(set(TYPE_SCALE_STEPS) - set(value))
        if off_scale or missing:
            raise ValueError(
                f"scaleSteps must map exactly the defined steps {TYPE_SCALE_STEPS}; "
                f"off-scale {off_scale}, missing {missing} (REQ-046)."
            )
        return value


class ColorTemplate(StructuralColumnsMixin, Base):
    """One theme: fixed color/font slots over a type scale (PI-007).

    ``templateType`` (``TEMPLATE_TYPES``) splits the curated set from
    user-created templates; ``userID`` is the owner association — null on
    system templates, mirroring ``gridView``. ``launchSetKey`` marks which
    of the four launch themes a system template is (null = not a launch
    theme; user templates are never in the launch set). ``colorSlots`` fills
    exactly the ``COLOR_SLOTS`` structure with color values; ``fontSlots``
    fills ``FONT_SLOTS`` with {stepKey, fontFamily, fontWeight} specs whose
    steps — like ``typeStepChoice``, the template's base step — must be
    steps of the linked type scale (the many-to-one colorTemplate ↔
    typeScale association). Templates carry NO layer precedence (FND-907,
    REQ-044): layering is exactly three fixed positional layers — org
    default → user choice → the active view's ``gridView.rowTheme`` — so
    position, never a number, decides what wins.
    """

    __tablename__ = "colorTemplate"
    __table_args__ = (
        Index(
            "uq_colorTemplate_system_name_live",
            "colorTemplateName",
            unique=True,
            sqlite_where=_LIVE_SYSTEM_TEMPLATE,
            postgresql_where=_LIVE_SYSTEM_TEMPLATE,
        ),
        Index(
            "uq_colorTemplate_owner_name_live",
            "userID",
            "colorTemplateName",
            unique=True,
            sqlite_where=_LIVE_USER_TEMPLATE,
            postgresql_where=_LIVE_USER_TEMPLATE,
        ),
    )

    color_template_id: Mapped[uuid.UUID] = mapped_column(
        "colorTemplateID", primary_key=True, default=uuid7
    )
    color_template_name: Mapped[str] = mapped_column(
        "colorTemplateName", String(200), nullable=False
    )
    # ``TEMPLATE_TYPES`` vocabulary, app-validated (DB-S7).
    template_type: Mapped[str] = mapped_column("templateType", String(50), nullable=False)
    # The template-owner association: null = system template.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        "userID", ForeignKey("appUser.userID"), default=None
    )
    # The colorTemplate ↔ typeScale association (many templates, one scale).
    type_scale_id: Mapped[uuid.UUID] = mapped_column(
        "typeScaleID", ForeignKey("typeScale.typeScaleID"), nullable=False
    )
    color_slots: Mapped[dict[str, Any]] = mapped_column(
        "colorSlots", JsonValue, nullable=False, default=dict
    )
    font_slots: Mapped[dict[str, Any]] = mapped_column(
        "fontSlots", JsonValue, nullable=False, default=dict
    )
    # ``LAUNCH_SETS`` vocabulary; null = not one of the four launch themes.
    launch_set_key: Mapped[str | None] = mapped_column("launchSetKey", String(50), default=None)
    # The template's base step — one of the linked scale's TYPE_SCALE_STEPS.
    type_step_choice: Mapped[str] = mapped_column(
        "typeStepChoice", String(20), nullable=False, default="md"
    )
    contrast_guardrail_behavior: Mapped[str] = mapped_column(
        "contrastGuardrailBehavior", String(20), nullable=False, default="warn"
    )

    type_scale: Mapped[TypeScale] = relationship(back_populates="color_templates")
    # The ordered theme ↔ rule association: rules evaluate first-match-wins
    # in this order, so the collection IS the evaluation order.
    formatting_rules: Mapped[list[ConditionalFormattingRule]] = relationship(
        back_populates="color_template",
        order_by="ConditionalFormattingRule.evaluation_order",
    )

    @validates("type_step_choice")
    def _reject_off_scale_choice(self, _key: str, step: str) -> str:
        # Same persistence-boundary backstop as TypeScale._reject_off_scale_steps.
        if step not in TYPE_SCALE_STEPS:
            raise ValueError(
                f"typeStepChoice must name a defined type-scale step "
                f"{TYPE_SCALE_STEPS}, got {step!r} — off-scale sizes are "
                f"prohibited (REQ-046)."
            )
        return step

    @validates("font_slots")
    def _reject_off_scale_font_steps(self, _key: str, value: dict[str, Any]) -> dict[str, Any]:
        # Off-scale step references only; spec shape (family, weight, exact
        # slot set) is the WTK-114 surface's field-shaped validation.
        for slot, spec in value.items():
            if not isinstance(spec, Mapping) or "stepKey" not in spec:
                continue
            if spec["stepKey"] not in TYPE_SCALE_STEPS:
                raise ValueError(
                    f"fontSlots.{slot} names step {spec['stepKey']!r}; font sizes "
                    f"must name a defined type-scale step {TYPE_SCALE_STEPS} "
                    f"(REQ-046)."
                )
        return value


class ConditionalFormattingRule(StructuralColumnsMixin, Base):
    """One conditional-formatting rule of a template (PI-007).

    Evaluation is first-match-wins: rules run in ``evaluationOrder``
    (1-based, unique per template among live rows) and the first whose
    condition holds applies — later rules never stack on top. The condition
    is ``conditionField`` ``conditionOperator`` ``conditionValue`` (value is
    null for the presence operators); ``conditionField`` must be a field the
    consuming view's data source exposes, API-validated like a view's
    displayed fields (REQ-019). ``effect`` names the fixed slot the rule
    repaints (``FORMATTING_EFFECTS``) and ``effectSlot`` the STATUS slot
    whose template color paints it — REQ-045: effects name status slots,
    never literal colors (FND-906), so a rule restyles WITH the theme, and
    switching templates recolors every rule coherently.
    """

    __tablename__ = "conditionalFormattingRule"
    __table_args__ = (
        Index(
            "uq_conditionalFormattingRule_template_order_live",
            "colorTemplateID",
            "evaluationOrder",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    conditional_formatting_rule_id: Mapped[uuid.UUID] = mapped_column(
        "conditionalFormattingRuleID", primary_key=True, default=uuid7
    )
    color_template_id: Mapped[uuid.UUID] = mapped_column(
        "colorTemplateID", ForeignKey("colorTemplate.colorTemplateID"), nullable=False
    )
    condition_field: Mapped[str] = mapped_column("conditionField", String(100), nullable=False)
    # ``CONDITION_OPERATORS`` vocabulary, app-validated (DB-S7).
    condition_operator: Mapped[str] = mapped_column(
        "conditionOperator", String(50), nullable=False
    )
    # JSON so a comparison value keeps its type (number/string/bool);
    # null for isEmpty/isNotEmpty.
    condition_value: Mapped[dict[str, Any] | None] = mapped_column(
        "conditionValue", JsonValue, default=None
    )
    # ``FORMATTING_EFFECTS`` vocabulary — the effect enum is slot-limited.
    effect: Mapped[str] = mapped_column("effect", String(50), nullable=False)
    # ``STATUS_COLOR_SLOTS`` vocabulary — REQ-045: the applied color is a
    # status slot of the owning template, never a literal value (FND-906).
    effect_slot: Mapped[str] = mapped_column("effectSlot", String(50), nullable=False)
    evaluation_order: Mapped[int] = mapped_column("evaluationOrder", nullable=False)

    color_template: Mapped[ColorTemplate] = relationship(back_populates="formatting_rules")


def shared_type_scale(session: Session) -> TypeScale:
    """The ONE persisted app-wide type scale (WTK-116, REQ-046).

    Returns the live seeded row named :data:`SHARED_TYPE_SCALE_NAME` — the
    single scale every template links and the WTK-114 type-scale surface
    serves/retunes. Excludes soft-deleted rows (DB-S3 default-read rule).
    Raises :class:`LookupError` if the seed (migration 0011) is absent —
    a broken deployment, not a normal state.
    """
    scale = session.scalars(
        select(TypeScale).where(
            TypeScale.type_scale_name == SHARED_TYPE_SCALE_NAME,
            TypeScale.deleted_at.is_(None),
        )
    ).one_or_none()
    if scale is None:
        raise LookupError(
            f"The shared type scale {SHARED_TYPE_SCALE_NAME!r} is not seeded; "
            f"run migrations (0011 persists it)."
        )
    return scale
