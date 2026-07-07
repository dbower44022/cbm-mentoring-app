"""Field-setting authority columns on the schema registry (WTK-056).

REQ-033/REQ-040 in schema form: the registry row IS the field setting — the
single authority every form applies — so it gains ``defaultValue`` (what
pre-populates a new record's field; JSON so every fieldType's default is
representable) and ``helpText`` (admin-maintained field-level help, rendered
on hover/focus, never hardcoded in a form). Both apply to built-in and
user-defined fields alike.

The 0006/0007 seeds ran while the registry table lacked these columns (the
seed defers ORM attributes the mid-chain table does not have), so this
migration reseeds every previously seeded entity to reconcile the new
metadata in the same change-set that adds it (REQ-050).

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from mentorapp.storage.crm_refs import CrmCompanyRef, CrmMentorRef
from mentorapp.storage.mentoring import ProgressGoal
from mentorapp.storage.registry_seed import seed_built_in_registry

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# Mirrors storage.base.JsonValue: JSONB on Postgres, plain JSON on SQLite.
_JSON_OBJECT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

# Explicit list, never a Base.registry sweep: under pytest the shared Base
# also carries throwaway test entities that must not be seeded here.
# PI-010 note (0014): the list names the classes 0006/0007 now seed — the
# entities 0014 renamed or retired reconcile their new metadata at 0014's
# own seed instead.
_SEEDED_ENTITIES = (
    CrmCompanyRef,
    CrmMentorRef,
    ProgressGoal,
)


def upgrade() -> None:
    with op.batch_alter_table("schemaRegistry", schema=None) as batch_op:
        batch_op.add_column(sa.Column("defaultValue", _JSON_OBJECT, nullable=True))
        batch_op.add_column(sa.Column("helpText", sa.String(length=2000), nullable=True))
    seed_built_in_registry(Session(bind=op.get_bind()), list(_SEEDED_ENTITIES))


def downgrade() -> None:
    with op.batch_alter_table("schemaRegistry", schema=None) as batch_op:
        batch_op.drop_column("helpText")
        batch_op.drop_column("defaultValue")
