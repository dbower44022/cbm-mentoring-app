"""Job progress and the notification bell (WTK-023).

Adds ``jobProgress`` to ``backgroundJob`` — the handler-written document
behind ``GET /jobs/{jobID}`` and the status bar (REQ-014) — and creates the
per-user ``notification`` table behind the header bell (REQ-014): typed
entries linked to the job whose terminal transition produced them, read
state stamped on view, retention expiry. Same structural rules as 0001/0002:
UUIDv7 app-generated keys (REQ-047), the eight structural system columns
(REQ-053), partial live-row indexes (REQ-052). Platform tables — no
schema-registry rows and no read views.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# Mirrors storage.base.JsonValue: JSONB on Postgres, plain JSON on SQLite.
_JSON_OBJECT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_LIVE = sa.text('"deletedAt" IS NULL')
_LIVE_UNREAD = sa.text('"deletedAt" IS NULL AND "readAt" IS NULL')
_LIVE_JOB_LINKED = sa.text('"deletedAt" IS NULL AND "jobID" IS NOT NULL')
_EXPIRING = sa.text('"notificationExpiresAt" IS NOT NULL')


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
    with op.batch_alter_table("backgroundJob", schema=None) as batch_op:
        batch_op.add_column(sa.Column("jobProgress", _JSON_OBJECT, nullable=True))

    op.create_table(
        "notification",
        sa.Column("notificationID", sa.Uuid(), nullable=False),
        sa.Column("userID", sa.Uuid(), nullable=False),
        sa.Column("notificationType", sa.String(length=50), nullable=False),
        sa.Column("notificationMessage", sa.String(length=2000), nullable=False),
        sa.Column("jobID", sa.Uuid(), nullable=True),
        sa.Column("readAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notificationExpiresAt", sa.DateTime(timezone=True), nullable=True),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["userID"], ["appUser.userID"], name=op.f("fk_notification_userID_appUser")
        ),
        sa.ForeignKeyConstraint(
            ["jobID"],
            ["backgroundJob.jobID"],
            name=op.f("fk_notification_jobID_backgroundJob"),
        ),
        sa.PrimaryKeyConstraint("notificationID", name=op.f("pk_notification")),
    )
    with op.batch_alter_table("notification", schema=None) as batch_op:
        batch_op.create_index(
            "ix_notification_bell_live",
            ["userID", "createdAt"],
            unique=False,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.create_index(
            "ix_notification_expiry",
            ["notificationExpiresAt"],
            unique=False,
            sqlite_where=_EXPIRING,
            postgresql_where=_EXPIRING,
        )
        batch_op.create_index(
            batch_op.f("ix_notification_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "ix_notification_unread",
            ["userID"],
            unique=False,
            sqlite_where=_LIVE_UNREAD,
            postgresql_where=_LIVE_UNREAD,
        )
        batch_op.create_index(
            "uq_notification_job_user_live",
            ["jobID", "userID"],
            unique=True,
            sqlite_where=_LIVE_JOB_LINKED,
            postgresql_where=_LIVE_JOB_LINKED,
        )


def downgrade() -> None:
    with op.batch_alter_table("notification", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_notification_job_user_live",
            sqlite_where=_LIVE_JOB_LINKED,
            postgresql_where=_LIVE_JOB_LINKED,
        )
        batch_op.drop_index(
            "ix_notification_unread",
            sqlite_where=_LIVE_UNREAD,
            postgresql_where=_LIVE_UNREAD,
        )
        batch_op.drop_index(batch_op.f("ix_notification_modifiedAt"))
        batch_op.drop_index(
            "ix_notification_expiry", sqlite_where=_EXPIRING, postgresql_where=_EXPIRING
        )
        batch_op.drop_index(
            "ix_notification_bell_live", sqlite_where=_LIVE, postgresql_where=_LIVE
        )

    op.drop_table("notification")
    with op.batch_alter_table("backgroundJob", schema=None) as batch_op:
        batch_op.drop_column("jobProgress")
