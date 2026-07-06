"""Color-template, type-scale, and conditional-formatting entities (WTK-111).

Implements the look-and-feel data model (PI-007): ``colorTemplate`` is one
curated or user-authored theme — its fixed color/font slot structure, the
launch-set membership (Standard/Compact/Large print/Dark), its chosen type
step, the contrast guardrail, and its layer precedence. ``typeScale`` is the
shared app-wide typography scale: a defined step set that every font size
must come from — off-scale sizes are prohibited (API-validated, like every
vocabulary here per DB-S7). ``conditionalFormattingRule`` is one rule of a
template's conditional formatting (the grid standard: "conditional
formatting lives in the theme"), evaluated first-match-wins in
``evaluationOrder``.

Associations: ``colorTemplate.typeScaleID`` is the many-to-one
colorTemplate ↔ typeScale association (the template's font slots and
``typeStepChoice`` must name steps of THAT scale);
``conditionalFormattingRule.colorTemplateID`` + ``evaluationOrder`` is the
ordered one-to-many theme ↔ rule association; ``rowThemeOverride`` is the
per-grid colorTemplate ↔ grid row-theme-override association — at most one
live override per grid.

These are platform tables (``StructuralColumnsMixin`` + ``Base``, not
``BaseEntity``): like ``grid`` and ``gridView`` they are app configuration,
get no schema-registry rows and no generated read views, and never surface
through the admin read surface. Every foreign-key column carries the exact
name of the primary key it references (DB-R2b).
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

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

# The fixed color-slot structure (PI-007): every template fills exactly these
# slots — a template never invents a slot, so every consumer (grid rows,
# selection, accents) reads a color that is guaranteed to exist.
COLOR_SLOTS: Final[tuple[str, ...]] = (
    "rowBackground",
    "alternateRowBackground",
    "rowText",
    "selectedRowBackground",
    "selectedRowText",
    "accent",
)

# The fixed font-slot structure: each slot names a step of the template's
# type scale (plus family/weight) — the slot set is as fixed as COLOR_SLOTS.
FONT_SLOTS: Final[tuple[str, ...]] = ("rowFont", "headerFont")

# The defined step set of the app-wide typography scale. A typeScale row maps
# each step to a concrete size; fonts choose steps, never raw sizes.
TYPE_SCALE_STEPS: Final[tuple[str, ...]] = ("xs", "sm", "md", "lg", "xl")

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
        "scaleSteps", JsonValue, nullable=False, default=dict
    )

    color_templates: Mapped[list[ColorTemplate]] = relationship(back_populates="type_scale")


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
    typeScale association). ``layerPrecedence`` orders templates when
    presentation layers stack (a view theme over a grid override): higher
    wins, 0 is the base-layer default.
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
    layer_precedence: Mapped[int] = mapped_column("layerPrecedence", nullable=False, default=0)

    type_scale: Mapped[TypeScale] = relationship(back_populates="color_templates")
    # The ordered theme ↔ rule association: rules evaluate first-match-wins
    # in this order, so the collection IS the evaluation order.
    formatting_rules: Mapped[list[ConditionalFormattingRule]] = relationship(
        back_populates="color_template",
        order_by="ConditionalFormattingRule.evaluation_order",
    )


class ConditionalFormattingRule(StructuralColumnsMixin, Base):
    """One conditional-formatting rule of a template (PI-007).

    Evaluation is first-match-wins: rules run in ``evaluationOrder``
    (1-based, unique per template among live rows) and the first whose
    condition holds applies — later rules never stack on top. The condition
    is ``conditionField`` ``conditionOperator`` ``conditionValue`` (value is
    null for the presence operators); ``conditionField`` must be a field the
    consuming view's data source exposes, API-validated like a view's
    displayed fields (REQ-019). ``effect`` names the fixed slot the rule
    repaints (``FORMATTING_EFFECTS``) and ``effectColor`` the color applied.
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
    effect_color: Mapped[str] = mapped_column("effectColor", String(50), nullable=False)
    evaluation_order: Mapped[int] = mapped_column("evaluationOrder", nullable=False)

    color_template: Mapped[ColorTemplate] = relationship(back_populates="formatting_rules")


class RowThemeOverride(StructuralColumnsMixin, Base):
    """The per-grid colorTemplate ↔ grid row-theme-override association (WTK-111).

    At most one live override per grid: the named template replaces the
    standard row theme for every view of that grid that does not carry its
    own ``rowTheme`` — resolution order is the API's; this row only records
    the association.
    """

    __tablename__ = "rowThemeOverride"
    __table_args__ = (
        Index(
            "uq_rowThemeOverride_gridID_live",
            "gridID",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    row_theme_override_id: Mapped[uuid.UUID] = mapped_column(
        "rowThemeOverrideID", primary_key=True, default=uuid7
    )
    grid_id: Mapped[uuid.UUID] = mapped_column(
        "gridID", ForeignKey("grid.gridID"), nullable=False
    )
    color_template_id: Mapped[uuid.UUID] = mapped_column(
        "colorTemplateID", ForeignKey("colorTemplate.colorTemplateID"), nullable=False
    )
