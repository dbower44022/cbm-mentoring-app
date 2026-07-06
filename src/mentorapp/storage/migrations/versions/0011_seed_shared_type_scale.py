"""Seed the ONE shared app-wide type scale (WTK-116, PI-007).

``typeScale`` (0010) shipped as schema only; the WTK-114 surface serves and
retunes THE single shared row (``GET/PATCH /theming/type-scale``, REQ-046),
so that row must exist from first boot. Seeded here with the design-default
step sizes (``TYPE_SCALE_DEFAULT_SIZES``) under the well-known name
``SHARED_TYPE_SCALE_NAME`` — the 0007 precedent: built-in data lands in the
same source-controlled change-set as the schema it rides. The row carries
exactly the defined step set, so no off-scale step is ever persisted; with
no create-scale endpoint, seed + name enforce the singleton.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from mentorapp.storage.base import utcnow, uuid7
from mentorapp.storage.theming import SHARED_TYPE_SCALE_NAME, TYPE_SCALE_DEFAULT_SIZES

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

# Mirrors storage.base.JsonValue: JSONB on Postgres, plain JSON on SQLite.
_JSON_OBJECT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

# Lightweight table stub (not the ORM model): the insert stays correct against
# the 0011-era schema even after the model grows columns with defaults.
_TYPE_SCALE = sa.table(
    "typeScale",
    sa.column("typeScaleID", sa.Uuid()),
    sa.column("typeScaleName", sa.String(length=200)),
    sa.column("scaleSteps", _JSON_OBJECT),
    sa.column("createdAt", sa.DateTime(timezone=True)),
    sa.column("modifiedAt", sa.DateTime(timezone=True)),
    sa.column("rowVersion", sa.Integer()),
    sa.column("customAttributes", _JSON_OBJECT),
)


def upgrade() -> None:
    # createdBy/modifiedBy stay NULL: a seed migration has no acting user
    # (the StructuralColumnsMixin contract).
    now = utcnow()
    op.bulk_insert(
        _TYPE_SCALE,
        [
            {
                "typeScaleID": uuid7(),
                "typeScaleName": SHARED_TYPE_SCALE_NAME,
                "scaleSteps": dict(TYPE_SCALE_DEFAULT_SIZES),
                "createdAt": now,
                "modifiedAt": now,
                "rowVersion": 1,
                "customAttributes": {},
            }
        ],
    )


def downgrade() -> None:
    # Hard delete is correct here: a schema rollback removes the seed it made.
    op.execute(
        _TYPE_SCALE.delete().where(_TYPE_SCALE.c.typeScaleName == SHARED_TYPE_SCALE_NAME)
    )
