"""Field-setting authority columns on the schema registry (WTK-056).

REQ-033/REQ-040 in schema form: the registry row IS the field setting — the
single authority every form applies — so it gains ``defaultValue`` (what
pre-populates a new record's field; JSON so every fieldType's default is
representable) and ``helpText`` (admin-maintained field-level help, rendered
on hover/focus, never hardcoded in a form). Both apply to built-in and
user-defined fields alike. No reseed here: no current entity column declares
a default or help text, so existing rows correctly carry NULL; a definition
that does declare one seeds in its own change-set (REQ-050).

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# Mirrors storage.base.JsonValue: JSONB on Postgres, plain JSON on SQLite.
_JSON_OBJECT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("schemaRegistry", schema=None) as batch_op:
        batch_op.add_column(sa.Column("defaultValue", _JSON_OBJECT, nullable=True))
        batch_op.add_column(sa.Column("helpText", sa.String(length=2000), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("schemaRegistry", schema=None) as batch_op:
        batch_op.drop_column("helpText")
        batch_op.drop_column("defaultValue")
