"""Help mapping + settings singleton (WTK-103, REQ-043).

The PI-006 help-system persistence: ``helpMapping`` is one admin-configured
page → docs-platform-URL mapping (help content lives OUTSIDE the app,
SKL-116 — these rows are links, never text), unique per live
``(sourceType, sourceIdentifier)`` surface. ``helpSettings`` is the singleton
fallback document (help home URL + the ``{sourceType}/{sourceIdentifier}``
default URL pattern), SEEDED here with empty values so the row exists from
first boot — "not configured yet" is a row state the resolve endpoint
explains, not a missing row, and the settings PATCH always has a
``rowVersion`` to address (DB-S4; the 0011 seed precedent). Same structural
rules as 0012: UUIDv7 app-generated keys (REQ-047), the eight structural
system columns (REQ-053), partial live-row indexes (REQ-052). Platform
tables — no schema-registry rows and no read views.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from mentorapp.storage.base import utcnow, uuid7

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# Mirrors storage.base.JsonValue: JSONB on Postgres, plain JSON on SQLite.
_JSON_OBJECT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_LIVE = sa.text('"deletedAt" IS NULL')

# Lightweight table stub (not the ORM model): the seed insert stays correct
# against the 0013-era schema even after the model grows columns with defaults.
_HELP_SETTINGS = sa.table(
    "helpSettings",
    sa.column("helpSettingsID", sa.Uuid()),
    sa.column("helpHomeURL", sa.String(length=2000)),
    sa.column("defaultURLPattern", sa.String(length=2000)),
    sa.column("createdAt", sa.DateTime(timezone=True)),
    sa.column("modifiedAt", sa.DateTime(timezone=True)),
    sa.column("rowVersion", sa.Integer()),
    sa.column("customAttributes", _JSON_OBJECT),
)


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
        "helpMapping",
        sa.Column("helpMappingID", sa.Uuid(), nullable=False),
        sa.Column("sourceType", sa.String(length=20), nullable=False),
        sa.Column("sourceIdentifier", sa.String(length=200), nullable=False),
        sa.Column("helpURL", sa.String(length=2000), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("helpMappingID", name=op.f("pk_helpMapping")),
    )
    with op.batch_alter_table("helpMapping", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_helpMapping_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_helpMapping_source_live",
            ["sourceType", "sourceIdentifier"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.create_table(
        "helpSettings",
        sa.Column("helpSettingsID", sa.Uuid(), nullable=False),
        sa.Column("helpHomeURL", sa.String(length=2000), nullable=False),
        sa.Column("defaultURLPattern", sa.String(length=2000), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("helpSettingsID", name=op.f("pk_helpSettings")),
    )
    with op.batch_alter_table("helpSettings", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_helpSettings_modifiedAt"), ["modifiedAt"], unique=False
        )

    # createdBy/modifiedBy stay NULL: a seed migration has no acting user
    # (the StructuralColumnsMixin contract). Empty strings = "not configured
    # yet" — the admin fills them in through PATCH /help/settings.
    now = utcnow()
    op.bulk_insert(
        _HELP_SETTINGS,
        [
            {
                "helpSettingsID": uuid7(),
                "helpHomeURL": "",
                "defaultURLPattern": "",
                "createdAt": now,
                "modifiedAt": now,
                "rowVersion": 1,
                "customAttributes": {},
            }
        ],
    )


def downgrade() -> None:
    # Dropping the table drops the seed with it — no separate delete needed.
    with op.batch_alter_table("helpSettings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_helpSettings_modifiedAt"))
    op.drop_table("helpSettings")

    with op.batch_alter_table("helpMapping", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_helpMapping_source_live", sqlite_where=_LIVE, postgresql_where=_LIVE
        )
        batch_op.drop_index(batch_op.f("ix_helpMapping_modifiedAt"))
    op.drop_table("helpMapping")
