"""CRM reference entities: crmClientRef, crmEngagementRef, crmMentorRef (WTK-150).

The REQ-062 ownership boundary in schema form: the CRM stays the system of
record for client/engagement/mentor master data, and each of these tables is
an identity anchor only — an entity-named UUIDv7 key for app-owned rows
(REQ-063) to reference, plus the CRM record's own id — with deliberately no
master-data columns. Same structural rules as 0001..0005: UUIDv7
app-generated keys (REQ-047), the eight structural system columns (REQ-053),
partial live-row unique indexes (REQ-052). Unlike the platform tables, these
are domain entities: their built-in schema-registry rows are seeded here, in
the same change-set that adds the columns (REQ-050), from the column-site
definitions in ``storage/crm_refs.py``.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from mentorapp.storage.crm_refs import CrmClientRef, CrmEngagementRef, CrmMentorRef
from mentorapp.storage.registry_seed import seed_built_in_registry

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# Mirrors storage.base.JsonValue: JSONB on Postgres, plain JSON on SQLite.
_JSON_OBJECT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_LIVE = sa.text('"deletedAt" IS NULL')

_REF_ENTITIES = (CrmClientRef, CrmEngagementRef, CrmMentorRef)
# (table, entity-named UUID key, CRM record id column) — one anchor per entity.
_REF_TABLES = (
    ("crmClientRef", "crmClientRefID", "crmClientID"),
    ("crmEngagementRef", "crmEngagementRefID", "crmEngagementID"),
    ("crmMentorRef", "crmMentorRefID", "crmMentorID"),
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
    for table_name, key_column, crm_id_column in _REF_TABLES:
        op.create_table(
            table_name,
            sa.Column(key_column, sa.Uuid(), nullable=False),
            sa.Column(crm_id_column, sa.String(length=64), nullable=False),
            *_structural_columns(),
            sa.PrimaryKeyConstraint(key_column, name=op.f(f"pk_{table_name}")),
        )
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f(f"ix_{table_name}_modifiedAt"), ["modifiedAt"], unique=False
            )
            # One live anchor per CRM record; a soft-deleted anchor never
            # blocks re-anchoring the same record (REQ-052).
            batch_op.create_index(
                f"uq_{table_name}_{crm_id_column}_live",
                [crm_id_column],
                unique=True,
                sqlite_where=_LIVE,
                postgresql_where=_LIVE,
            )

    # First domain entities in the chain: seed their built-in registry rows in
    # the same change-set that adds the columns (REQ-050); flush only — the
    # migration transaction owns the commit.
    seed_built_in_registry(Session(bind=op.get_bind()), list(_REF_ENTITIES))


def downgrade() -> None:
    # Mirror the upgrade's seed exactly: remove the built-in registry rows
    # these entities own, leaving user-defined rows untouched.
    schema_registry = sa.table(
        "schemaRegistry",
        sa.column("entityType", sa.String()),
        sa.column("userDefinedFlag", sa.Boolean()),
    )
    op.execute(
        sa.delete(schema_registry).where(
            schema_registry.c.entityType.in_([name for name, _, _ in _REF_TABLES]),
            schema_registry.c.userDefinedFlag.is_(False),
        )
    )
    for table_name, _key_column, crm_id_column in reversed(_REF_TABLES):
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.drop_index(
                f"uq_{table_name}_{crm_id_column}_live",
                sqlite_where=_LIVE,
                postgresql_where=_LIVE,
            )
            batch_op.drop_index(batch_op.f(f"ix_{table_name}_modifiedAt"))
        op.drop_table(table_name)
