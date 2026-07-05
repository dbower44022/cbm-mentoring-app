"""Admin messages and per-user receipts (WTK-192, REQ-011).

Creates ``adminMessage`` (broadcast content: admin-set expiration, priority,
requiresAcknowledgment flag — posted-at/by are the structural
``createdAt``/``createdBy``) and ``adminMessageReceipt`` (per-user read and
acknowledgment stamps; receipts survive message expiration, so the admin
acknowledgment report keeps answering after the message leaves Home). Same
structural rules as 0001-0003: UUIDv7 app-generated keys (REQ-047), the eight
structural system columns (REQ-053), partial live-row indexes (REQ-052).
Platform tables — no schema-registry rows and no read views, exactly like the
WTK-023 ``notification`` table these deliberately stay separate from.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Mirrors storage.base.JsonValue: JSONB on Postgres, plain JSON on SQLite.
_JSON_OBJECT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_LIVE = sa.text('"deletedAt" IS NULL')


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
        "adminMessage",
        sa.Column("adminMessageID", sa.Uuid(), nullable=False),
        sa.Column("messageTitle", sa.String(length=200), nullable=False),
        sa.Column("messageBody", sa.String(length=2000), nullable=False),
        sa.Column("messagePriority", sa.String(length=50), nullable=False),
        sa.Column("requiresAcknowledgmentFlag", sa.Boolean(), nullable=False),
        sa.Column("messageExpiresAt", sa.DateTime(timezone=True), nullable=True),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("adminMessageID", name=op.f("pk_adminMessage")),
    )
    with op.batch_alter_table("adminMessage", schema=None) as batch_op:
        batch_op.create_index(
            "ix_adminMessage_dashlet_live",
            ["createdAt"],
            unique=False,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.create_index(
            batch_op.f("ix_adminMessage_modifiedAt"), ["modifiedAt"], unique=False
        )

    op.create_table(
        "adminMessageReceipt",
        sa.Column("adminMessageReceiptID", sa.Uuid(), nullable=False),
        sa.Column("adminMessageID", sa.Uuid(), nullable=False),
        sa.Column("userID", sa.Uuid(), nullable=False),
        sa.Column("messageReadAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("messageAcknowledgedAt", sa.DateTime(timezone=True), nullable=True),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["adminMessageID"],
            ["adminMessage.adminMessageID"],
            name=op.f("fk_adminMessageReceipt_adminMessageID_adminMessage"),
        ),
        sa.ForeignKeyConstraint(
            ["userID"],
            ["appUser.userID"],
            name=op.f("fk_adminMessageReceipt_userID_appUser"),
        ),
        sa.PrimaryKeyConstraint("adminMessageReceiptID", name=op.f("pk_adminMessageReceipt")),
    )
    with op.batch_alter_table("adminMessageReceipt", schema=None) as batch_op:
        batch_op.create_index(
            "ix_adminMessageReceipt_user_live",
            ["userID"],
            unique=False,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.create_index(
            batch_op.f("ix_adminMessageReceipt_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_adminMessageReceipt_message_user_live",
            ["adminMessageID", "userID"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )


def downgrade() -> None:
    with op.batch_alter_table("adminMessageReceipt", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_adminMessageReceipt_message_user_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_adminMessageReceipt_modifiedAt"))
        batch_op.drop_index(
            "ix_adminMessageReceipt_user_live", sqlite_where=_LIVE, postgresql_where=_LIVE
        )
    op.drop_table("adminMessageReceipt")

    with op.batch_alter_table("adminMessage", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_adminMessage_modifiedAt"))
        batch_op.drop_index(
            "ix_adminMessage_dashlet_live", sqlite_where=_LIVE, postgresql_where=_LIVE
        )
    op.drop_table("adminMessage")
