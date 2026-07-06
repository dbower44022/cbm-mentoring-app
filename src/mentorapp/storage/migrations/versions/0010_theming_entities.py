"""Color-template, type-scale, and conditional-formatting entities (WTK-111, PI-007).

Creates the look-and-feel platform tables: ``typeScale`` (the shared
app-wide typography scale), ``colorTemplate`` (fixed color/font slots over
the UI design's 15-slot + 2-font vocabulary — FND-905, REQ-044 — launch
set, type-step choice, contrast guardrail), and
``conditionalFormattingRule`` (first-match-wins per template; its
``effectSlot`` names a status color slot, never a literal color — FND-906,
REQ-045). NO ``rowThemeOverride`` table and no ``layerPrecedence`` column
(FND-907, REQ-044/REQ-018): layering is exactly three fixed positional
layers, and the third already lives on ``gridView.rowTheme`` (0005). Same
structural rules as 0001/0005: UUIDv7 app-generated keys (REQ-047), the
eight structural system columns (REQ-053), and partial live-row indexes
(REQ-052). Platform tables — no schema-registry rows and no read views.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# Mirrors storage.base.JsonValue: JSONB on Postgres, plain JSON on SQLite.
_JSON_OBJECT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_LIVE = sa.text('"deletedAt" IS NULL')
# The colorTemplate name-uniqueness split: system templates share one
# namespace, a user's own templates share theirs (mirrors gridView).
_LIVE_SYSTEM_TEMPLATE = sa.text('"deletedAt" IS NULL AND "userID" IS NULL')
_LIVE_USER_TEMPLATE = sa.text('"deletedAt" IS NULL AND "userID" IS NOT NULL')


def _structural_columns() -> list[sa.Column]:
    return [
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdBy", sa.Uuid(), nullable=True),
        sa.Column("modifiedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modifiedBy", sa.Uuid(), nullable=True),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletedBy", sa.Uuid(), nullable=True),
        sa.Column("rowVersion", sa.Integer(), nullable=False),
        sa.Column("customAttributes", _JSON_OBJECT, nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "typeScale",
        sa.Column("typeScaleID", sa.Uuid(), nullable=False),
        sa.Column("typeScaleName", sa.String(length=200), nullable=False),
        sa.Column("scaleSteps", _JSON_OBJECT, nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("typeScaleID", name=op.f("pk_typeScale")),
    )
    with op.batch_alter_table("typeScale", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_typeScale_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_typeScale_typeScaleName_live",
            ["typeScaleName"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.create_table(
        "colorTemplate",
        sa.Column("colorTemplateID", sa.Uuid(), nullable=False),
        sa.Column("colorTemplateName", sa.String(length=200), nullable=False),
        sa.Column("templateType", sa.String(length=50), nullable=False),
        sa.Column("userID", sa.Uuid(), nullable=True),
        sa.Column("typeScaleID", sa.Uuid(), nullable=False),
        sa.Column("colorSlots", _JSON_OBJECT, nullable=False),
        sa.Column("fontSlots", _JSON_OBJECT, nullable=False),
        sa.Column("launchSetKey", sa.String(length=50), nullable=True),
        sa.Column("typeStepChoice", sa.String(length=20), nullable=False),
        sa.Column("contrastGuardrailBehavior", sa.String(length=20), nullable=False),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["userID"], ["appUser.userID"], name=op.f("fk_colorTemplate_userID_appUser")
        ),
        sa.ForeignKeyConstraint(
            ["typeScaleID"],
            ["typeScale.typeScaleID"],
            name=op.f("fk_colorTemplate_typeScaleID_typeScale"),
        ),
        sa.PrimaryKeyConstraint("colorTemplateID", name=op.f("pk_colorTemplate")),
    )
    with op.batch_alter_table("colorTemplate", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_colorTemplate_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_colorTemplate_system_name_live",
            ["colorTemplateName"],
            unique=True,
            sqlite_where=_LIVE_SYSTEM_TEMPLATE,
            postgresql_where=_LIVE_SYSTEM_TEMPLATE,
        )
        batch_op.create_index(
            "uq_colorTemplate_owner_name_live",
            ["userID", "colorTemplateName"],
            unique=True,
            sqlite_where=_LIVE_USER_TEMPLATE,
            postgresql_where=_LIVE_USER_TEMPLATE,
        )

    op.create_table(
        "conditionalFormattingRule",
        sa.Column("conditionalFormattingRuleID", sa.Uuid(), nullable=False),
        sa.Column("colorTemplateID", sa.Uuid(), nullable=False),
        sa.Column("conditionField", sa.String(length=100), nullable=False),
        sa.Column("conditionOperator", sa.String(length=50), nullable=False),
        sa.Column("conditionValue", _JSON_OBJECT, nullable=True),
        sa.Column("effect", sa.String(length=50), nullable=False),
        # FND-906: the applied color names a status slot, never a hex literal.
        sa.Column("effectSlot", sa.String(length=50), nullable=False),
        sa.Column("evaluationOrder", sa.Integer(), nullable=False),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["colorTemplateID"],
            ["colorTemplate.colorTemplateID"],
            name=op.f("fk_conditionalFormattingRule_colorTemplateID_colorTemplate"),
        ),
        sa.PrimaryKeyConstraint(
            "conditionalFormattingRuleID", name=op.f("pk_conditionalFormattingRule")
        ),
    )
    with op.batch_alter_table("conditionalFormattingRule", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_conditionalFormattingRule_modifiedAt"),
            ["modifiedAt"],
            unique=False,
        )
        batch_op.create_index(
            "uq_conditionalFormattingRule_template_order_live",
            ["colorTemplateID", "evaluationOrder"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )


def downgrade() -> None:
    with op.batch_alter_table("conditionalFormattingRule", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_conditionalFormattingRule_template_order_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_conditionalFormattingRule_modifiedAt"))
    op.drop_table("conditionalFormattingRule")

    with op.batch_alter_table("colorTemplate", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_colorTemplate_owner_name_live",
            sqlite_where=_LIVE_USER_TEMPLATE,
            postgresql_where=_LIVE_USER_TEMPLATE,
        )
        batch_op.drop_index(
            "uq_colorTemplate_system_name_live",
            sqlite_where=_LIVE_SYSTEM_TEMPLATE,
            postgresql_where=_LIVE_SYSTEM_TEMPLATE,
        )
        batch_op.drop_index(batch_op.f("ix_colorTemplate_modifiedAt"))
    op.drop_table("colorTemplate")

    with op.batch_alter_table("typeScale", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_typeScale_typeScaleName_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_typeScale_modifiedAt"))
    op.drop_table("typeScale")
