"""Org-default startup preference: mentors land on Engagements (WTK-233).

Doug's REQ-072 ruling — the mentor landing is the Engagements panel's "My
Active Engagements" view — expressed as the org-default ``shell.startup``
preference row (the REQ-060 pair; any user's own row overrides it). Seeded
insert-if-absent by :func:`mentorapp.api.panel_catalog.seed_startup_default`:
an admin-edited org default is data and is never swept back by a re-run.

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from mentorapp.api.panel_catalog import seed_startup_default
from mentorapp.ui.home_panel import STARTUP_PREFERENCE_KEY

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    seed_startup_default(Session(bind=op.get_bind()))


def downgrade() -> None:
    # Remove exactly what the upgrade seeded: the org-default row (user_id
    # null). User-owned startup rows are user data and survive.
    preference = sa.table(
        "userPreference",
        sa.column("preferenceKey", sa.String),
        sa.column("userID", sa.Uuid),
    )
    op.execute(
        sa.delete(preference).where(
            preference.c.preferenceKey == STARTUP_PREFERENCE_KEY,
            preference.c.userID.is_(None),
        )
    )
