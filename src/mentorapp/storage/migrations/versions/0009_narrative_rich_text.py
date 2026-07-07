"""Narrative columns adopt the rich-text registry type (WTK-205).

REQ-090's deferred wiring (the WTK-204 delta design in ``ui.entry_editors``):
the four mentoring narrative columns — ``meetingNoteBody``,
``nextStepDescription``, ``progressGoalDescription``, ``sessionLogSummary`` —
retype ``text`` → ``richText`` so the one rich-text control keys on the
registry row, never a UI-side list of field names. The 0007/0008 seeds
registered them as ``text`` (the String-column derivation); reseeding the
mentoring entities reconciles the live rows against the source-controlled
declarations in the same change-set that retypes them (REQ-050).

PI-010 note (0014): of the four retyped entities only ``progressGoal``
still exists under its 0009 name — 0014 reconciled ``sessionLog`` into
``session`` and folded ``meetingNote``/``nextStep`` onto the session's
rich-text fields — so this reseed now covers :class:`ProgressGoal` alone.
The downgrade keeps the original four-row scope: a pre-0014 database
downgraded through 0014 carries exactly those rows.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from mentorapp.storage.mentoring import ProgressGoal
from mentorapp.storage.registry_seed import seed_built_in_registry

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

# Explicit list, never a Base.registry sweep (the 0008 stance): only the
# entities whose declarations changed are reconciled here.
_RETYPED_ENTITIES = (ProgressGoal,)

# (entityType, fieldName) of every 0009-era retyped row — the downgrade's
# exact scope on a pre-0014 database; on a fresh chain only the progressGoal
# row exists and the other updates match nothing.
_NARRATIVE_FIELDS = (
    ("meetingNote", "meetingNoteBody"),
    ("nextStep", "nextStepDescription"),
    ("progressGoal", "progressGoalDescription"),
    ("sessionLog", "sessionLogSummary"),
)


def upgrade() -> None:
    seed_built_in_registry(Session(bind=op.get_bind()), list(_RETYPED_ENTITIES))


def downgrade() -> None:
    # A reseed would re-read the (now richText) declarations, so the
    # downgrade restores the pre-0009 type directly on the four rows.
    registry = sa.table(
        "schemaRegistry",
        sa.column("entityType", sa.String),
        sa.column("fieldName", sa.String),
        sa.column("fieldType", sa.String),
    )
    for entity_type, field_name in _NARRATIVE_FIELDS:
        op.execute(
            registry.update()
            .where(
                registry.c.entityType == entity_type,
                registry.c.fieldName == field_name,
            )
            .values(fieldType="text")
        )
