"""Application-owned mentoring entities + the registry's DB-R2b shape (WTK-156).

REQ-063 in schema form: meetingNote, nextStep, progressGoal, and sessionLog
are owned by the application store (ownership side "application", declared in
``storage/mentoring.py`` and enforced by ``BaseEntity``), with
``sessionLog.crmEngagementRefID`` the many-to-one association to the REQ-062
engagement anchor. Same structural rules as 0001..0006; registry rows are
seeded in the same change-set that adds the columns (REQ-050).

That association is the schema's first DB-R2b key re-appearance — the FK
carries the identical name as the anchor PK it references — so the registry's
unique index moves from ``fieldName`` alone to ``(entityType, fieldName)``;
system-wide uniqueness for non-R2b names is enforced by the seed, which no
index can express. The index swap must precede the seed: under the old index
sessionLog's ``crmEngagementRefID`` row cannot exist.

PI-010 note (0014): the seed references the LIVE ORM classes, and 0014
reconciled ``sessionLog`` into the app-owned ``session`` entity and folded
``meetingNote``/``nextStep`` onto the session's rich-text fields (REQ-074/
REQ-082), so only :class:`ProgressGoal` still exists to seed here. The
0007-era tables are still created verbatim below — 0014 renames or retires
them — and their registry rows now first exist in their current shape at
0014's seed.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from mentorapp.storage.mentoring import ProgressGoal
from mentorapp.storage.registry_seed import seed_built_in_registry

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# Mirrors storage.base.JsonValue: JSONB on Postgres, plain JSON on SQLite.
_JSON_OBJECT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_LIVE = sa.text('"deletedAt" IS NULL')

_MENTORING_ENTITIES = (ProgressGoal,)
# The 0007-era table names, for the downgrade's registry cleanup: a
# pre-0014 database downgraded through 0014 carries rows under these names.
_LEGACY_ENTITY_TYPES = ("meetingNote", "nextStep", "progressGoal", "sessionLog")


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
    with op.batch_alter_table("schemaRegistry", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_schemaRegistry_fieldName_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(
            "ix_schemaRegistry_entityType_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        # The unique index's leading column also serves the GET /schema/{entity}
        # scan the dropped entityType index used to cover.
        batch_op.create_index(
            "uq_schemaRegistry_entity_fieldName_live",
            ["entityType", "fieldName"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.create_table(
        "meetingNote",
        sa.Column("meetingNoteID", sa.Uuid(), nullable=False),
        sa.Column("meetingNoteBody", sa.String(length=4000), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("meetingNoteID", name=op.f("pk_meetingNote")),
    )
    op.create_table(
        "nextStep",
        sa.Column("nextStepID", sa.Uuid(), nullable=False),
        sa.Column("nextStepDescription", sa.String(length=2000), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("nextStepID", name=op.f("pk_nextStep")),
    )
    op.create_table(
        "progressGoal",
        sa.Column("progressGoalID", sa.Uuid(), nullable=False),
        sa.Column("progressGoalDescription", sa.String(length=2000), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("progressGoalID", name=op.f("pk_progressGoal")),
    )
    op.create_table(
        "sessionLog",
        sa.Column("sessionLogID", sa.Uuid(), nullable=False),
        sa.Column("crmEngagementRefID", sa.Uuid(), nullable=False),
        sa.Column("sessionLogDate", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sessionLogSummary", sa.String(length=4000), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("sessionLogID", name=op.f("pk_sessionLog")),
        sa.ForeignKeyConstraint(
            ["crmEngagementRefID"],
            ["crmEngagementRef.crmEngagementRefID"],
            name=op.f("fk_sessionLog_crmEngagementRefID_crmEngagementRef"),
        ),
    )

    for table_name in ("meetingNote", "nextStep", "progressGoal", "sessionLog"):
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f(f"ix_{table_name}_modifiedAt"), ["modifiedAt"], unique=False
            )
    with op.batch_alter_table("sessionLog", schema=None) as batch_op:
        # The "sessions for this engagement" read (REQ-063), live rows only.
        batch_op.create_index(
            "ix_sessionLog_crmEngagementRefID_live",
            ["crmEngagementRefID"],
            unique=False,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    seed_built_in_registry(Session(bind=op.get_bind()), list(_MENTORING_ENTITIES))


def downgrade() -> None:
    schema_registry = sa.table(
        "schemaRegistry",
        sa.column("entityType", sa.String()),
        sa.column("userDefinedFlag", sa.Boolean()),
    )
    op.execute(
        sa.delete(schema_registry).where(
            schema_registry.c.entityType.in_(list(_LEGACY_ENTITY_TYPES)),
            schema_registry.c.userDefinedFlag.is_(False),
        )
    )

    with op.batch_alter_table("sessionLog", schema=None) as batch_op:
        batch_op.drop_index(
            "ix_sessionLog_crmEngagementRefID_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
    for table_name in ("sessionLog", "progressGoal", "nextStep", "meetingNote"):
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.drop_index(batch_op.f(f"ix_{table_name}_modifiedAt"))
        op.drop_table(table_name)

    # Safe only because the R2b duplicate rows were deleted above with
    # sessionLog's registry rows — every remaining fieldName is unique again.
    with op.batch_alter_table("schemaRegistry", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_schemaRegistry_entity_fieldName_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.create_index(
            "ix_schemaRegistry_entityType_live",
            ["entityType"],
            unique=False,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.create_index(
            "uq_schemaRegistry_fieldName_live",
            ["fieldName"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
