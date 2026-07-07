"""Session conferencing + transcript-automation columns (WTK-170/180, PI-010).

The REQ-080/REQ-083 automation lands three columns on ``session``:

- ``externalMeetingID`` — the conference platform's identifier for an
  app-created org meeting (:mod:`mentorapp.automation.conferencing`); what
  the transcript retrieval presents back to the platform. Null marks the
  REQ-079 pasted-link path.
- ``draftSummary`` / ``draftActionItems`` — the REQ-083 PROPOSAL columns:
  AI-drafted material the mentor reviews and accepts/edits into
  ``sessionNotes``/``actionItems``; automation never writes the entry fields
  themselves (the mentor stays the author of record). Rich text, same width
  as the entry fields they feed.

The reseed reconciles the session registry rows in the same change-set that
adds the columns (REQ-050), so the new fields are registry-described (the
draft columns as ``richText``) and the write engine accepts them.

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from mentorapp.storage.mentoring import MentoringSession
from mentorapp.storage.registry_seed import seed_built_in_registry

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

# Explicit list, never a Base.registry sweep (the 0008 stance): only the
# entity whose declaration changed is reconciled here.
_SEEDED_ENTITIES = (MentoringSession,)

_NEW_COLUMNS = ("externalMeetingID", "draftSummary", "draftActionItems")


def upgrade() -> None:
    with op.batch_alter_table("session", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("externalMeetingID", sa.String(length=200), nullable=True)
        )
        batch_op.add_column(sa.Column("draftSummary", sa.String(length=4000), nullable=True))
        batch_op.add_column(
            sa.Column("draftActionItems", sa.String(length=4000), nullable=True)
        )
    seed_built_in_registry(Session(bind=op.get_bind()), list(_SEEDED_ENTITIES))


def downgrade() -> None:
    with op.batch_alter_table("session", schema=None) as batch_op:
        for column_name in reversed(_NEW_COLUMNS):
            batch_op.drop_column(column_name)
    # Soft-retire the registry rows whose columns are gone (the 0014 stance:
    # rows remain the record that these fields once existed).
    registry = sa.table(
        "schemaRegistry",
        sa.column("entityType", sa.String),
        sa.column("fieldName", sa.String),
        sa.column("deletedAt", sa.DateTime(timezone=True)),
        sa.column("userDefinedFlag", sa.Boolean),
    )
    op.execute(
        registry.update()
        .where(
            registry.c.entityType == "session",
            registry.c.fieldName.in_(list(_NEW_COLUMNS)),
            registry.c.userDefinedFlag.is_(False),
            registry.c.deletedAt.is_(None),
        )
        .values(deletedAt=sa.func.now())
    )
