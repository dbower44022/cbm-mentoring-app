"""Persisted lookup-source bindings: the durable REQ-036 resolver store (PI-012).

The relationship-lookup design (``access/lookup_grants.py``) named ONE new
persisted fact — which data source governs an entity's lookup — and carried
it on an in-memory seam pending its storage-area home. This lands that home:
the ``lookupSourceBinding`` platform table (a sibling of ``dataSourceRoleGrant``
— access configuration, so no registry rows and no read view), seeded with
the CBM area bindings so a fresh database has working relationship type-ahead
out of the box (``client`` → ``mentorClients``, and the rest per
``MENTOR_LOOKUP_BINDINGS``). ``StoredLookupSources`` reads it live per
keystroke, so an administrator's re-bind takes effect on the next request.

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from mentorapp.access.mentoring import seed_mentor_lookup_bindings

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

_LIVE = sa.text('"deletedAt" IS NULL')
_JSON_OBJECT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


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
        "lookupSourceBinding",
        sa.Column("lookupSourceBindingID", sa.Uuid(), nullable=False),
        sa.Column("relatedEntityType", sa.String(length=100), nullable=False),
        sa.Column("dataSourceKey", sa.String(length=200), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("lookupSourceBindingID", name=op.f("pk_lookupSourceBinding")),
    )
    with op.batch_alter_table("lookupSourceBinding", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_lookupSourceBinding_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_lookupSourceBinding_entity_live",
            ["relatedEntityType"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    # The CBM area bindings (product config, the seed_mentor_access precedent):
    # a fresh database has working relationship lookups without an admin step.
    seed_mentor_lookup_bindings(Session(bind=op.get_bind()))


def downgrade() -> None:
    op.drop_table("lookupSourceBinding")
