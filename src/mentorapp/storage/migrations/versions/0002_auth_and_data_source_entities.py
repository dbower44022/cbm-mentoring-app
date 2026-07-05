"""Auth and data-source entities (WTK-001, reconciled to WTK-002/WTK-003).

Creates the auth/access platform tables: ``appUser``, ``authSession``,
``actionToken``, ``tokenAuditEvent``, ``accessGrant``, ``dataSource``, and
the ``userCrmAccount`` and ``dataSourceRoleGrant`` associations. Same
structural rules as 0001: UUIDv7 app-generated keys (REQ-047), the eight
structural system columns (REQ-053), and partial live-row indexes (REQ-052).
These are platform tables — no schema-registry rows and no read views, so
sessions, tokens, and grants never surface through the admin read surface.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
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
        "appUser",
        sa.Column("userID", sa.Uuid(), nullable=False),
        sa.Column("crmUserID", sa.String(length=100), nullable=False),
        sa.Column("username", sa.String(length=200), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("userID", name=op.f("pk_appUser")),
    )
    with op.batch_alter_table("appUser", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_appUser_modifiedAt"), ["modifiedAt"], unique=False)
        batch_op.create_index(
            "uq_appUser_crmUserID_live",
            ["crmUserID"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.create_index(
            "uq_appUser_username_live",
            ["username"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    # After appUser: actionToken carries a nullable userID FK. No signature
    # column and no reference index — the HMAC is recomputed per presentation
    # and lookup is by primary key (WTK-002).
    op.create_table(
        "actionToken",
        sa.Column("actionTokenID", sa.Uuid(), nullable=False),
        sa.Column("tokenAction", sa.String(length=100), nullable=False),
        sa.Column("tokenIdentity", sa.String(length=320), nullable=False),
        sa.Column("userID", sa.Uuid(), nullable=True),
        sa.Column("tokenExpiresAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tokenMaxUses", sa.Integer(), nullable=False),
        sa.Column("tokenUseCount", sa.Integer(), nullable=False),
        sa.Column("tokenRevokedAt", sa.DateTime(timezone=True), nullable=True),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["userID"], ["appUser.userID"], name=op.f("fk_actionToken_userID_appUser")
        ),
        sa.PrimaryKeyConstraint("actionTokenID", name=op.f("pk_actionToken")),
    )
    with op.batch_alter_table("actionToken", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_actionToken_modifiedAt"), ["modifiedAt"], unique=False
        )

    op.create_table(
        "tokenAuditEvent",
        sa.Column("tokenAuditEventID", sa.Uuid(), nullable=False),
        sa.Column("actionTokenID", sa.Uuid(), nullable=False),
        sa.Column("tokenEventName", sa.String(length=50), nullable=False),
        sa.Column("tokenEventOccurredAt", sa.DateTime(timezone=True), nullable=False),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["actionTokenID"],
            ["actionToken.actionTokenID"],
            name=op.f("fk_tokenAuditEvent_actionTokenID_actionToken"),
        ),
        sa.PrimaryKeyConstraint("tokenAuditEventID", name=op.f("pk_tokenAuditEvent")),
    )
    with op.batch_alter_table("tokenAuditEvent", schema=None) as batch_op:
        batch_op.create_index(
            "ix_tokenAuditEvent_actionTokenID_live",
            ["actionTokenID"],
            unique=False,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.create_index(
            batch_op.f("ix_tokenAuditEvent_modifiedAt"), ["modifiedAt"], unique=False
        )

    op.create_table(
        "dataSource",
        sa.Column("dataSourceID", sa.Uuid(), nullable=False),
        sa.Column("dataSourceKey", sa.String(length=200), nullable=False),
        sa.Column("dataSourceName", sa.String(length=200), nullable=False),
        sa.Column("dataSourceSql", sa.Text(), nullable=False),
        sa.Column("userRowFilter", sa.String(length=200), nullable=True),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("dataSourceID", name=op.f("pk_dataSource")),
    )
    with op.batch_alter_table("dataSource", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_dataSource_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_dataSource_dataSourceKey_live",
            ["dataSourceKey"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.create_table(
        "accessGrant",
        sa.Column("accessGrantID", sa.Uuid(), nullable=False),
        sa.Column("userID", sa.Uuid(), nullable=False),
        sa.Column("accessGrantKey", sa.String(length=100), nullable=False),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["userID"], ["appUser.userID"], name=op.f("fk_accessGrant_userID_appUser")
        ),
        sa.PrimaryKeyConstraint("accessGrantID", name=op.f("pk_accessGrant")),
    )
    with op.batch_alter_table("accessGrant", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_accessGrant_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_accessGrant_user_key_live",
            ["userID", "accessGrantKey"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.create_table(
        "authSession",
        sa.Column("authSessionID", sa.Uuid(), nullable=False),
        sa.Column("userID", sa.Uuid(), nullable=False),
        sa.Column("sessionSecretHash", sa.String(length=64), nullable=False),
        sa.Column("sessionState", sa.String(length=20), nullable=False),
        sa.Column("sessionExpiresAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sessionReauthDeadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sessionLastSeenAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sessionRoleNames", _JSON_OBJECT, nullable=True),
        sa.Column("crmCredentialEncrypted", sa.Text(), nullable=True),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["userID"], ["appUser.userID"], name=op.f("fk_authSession_userID_appUser")
        ),
        sa.PrimaryKeyConstraint("authSessionID", name=op.f("pk_authSession")),
    )
    with op.batch_alter_table("authSession", schema=None) as batch_op:
        batch_op.create_index(
            "ix_authSession_userID_live",
            ["userID"],
            unique=False,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.create_index(
            batch_op.f("ix_authSession_modifiedAt"), ["modifiedAt"], unique=False
        )

    op.create_table(
        "userCrmAccount",
        sa.Column("userCrmAccountID", sa.Uuid(), nullable=False),
        sa.Column("userID", sa.Uuid(), nullable=False),
        sa.Column("crmAccountID", sa.String(length=100), nullable=False),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["userID"], ["appUser.userID"], name=op.f("fk_userCrmAccount_userID_appUser")
        ),
        sa.PrimaryKeyConstraint("userCrmAccountID", name=op.f("pk_userCrmAccount")),
    )
    with op.batch_alter_table("userCrmAccount", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_userCrmAccount_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_userCrmAccount_user_account_live",
            ["userID", "crmAccountID"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.create_table(
        "dataSourceRoleGrant",
        sa.Column("dataSourceRoleGrantID", sa.Uuid(), nullable=False),
        sa.Column("dataSourceID", sa.Uuid(), nullable=False),
        sa.Column("roleName", sa.String(length=100), nullable=False),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["dataSourceID"],
            ["dataSource.dataSourceID"],
            name=op.f("fk_dataSourceRoleGrant_dataSourceID_dataSource"),
        ),
        sa.PrimaryKeyConstraint("dataSourceRoleGrantID", name=op.f("pk_dataSourceRoleGrant")),
    )
    with op.batch_alter_table("dataSourceRoleGrant", schema=None) as batch_op:
        batch_op.create_index(
            "ix_dataSourceRoleGrant_roleName_live",
            ["roleName"],
            unique=False,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.create_index(
            batch_op.f("ix_dataSourceRoleGrant_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_dataSourceRoleGrant_source_role_live",
            ["dataSourceID", "roleName"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )


def downgrade() -> None:
    with op.batch_alter_table("dataSourceRoleGrant", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_dataSourceRoleGrant_source_role_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_dataSourceRoleGrant_modifiedAt"))
        batch_op.drop_index(
            "ix_dataSourceRoleGrant_roleName_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.drop_table("dataSourceRoleGrant")
    with op.batch_alter_table("userCrmAccount", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_userCrmAccount_user_account_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_userCrmAccount_modifiedAt"))

    op.drop_table("userCrmAccount")
    with op.batch_alter_table("authSession", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_authSession_modifiedAt"))
        batch_op.drop_index(
            "ix_authSession_userID_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.drop_table("authSession")
    with op.batch_alter_table("accessGrant", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_accessGrant_user_key_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_accessGrant_modifiedAt"))

    op.drop_table("accessGrant")
    with op.batch_alter_table("dataSource", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_dataSource_dataSourceKey_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_dataSource_modifiedAt"))

    op.drop_table("dataSource")
    with op.batch_alter_table("appUser", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_appUser_username_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(
            "uq_appUser_crmUserID_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_appUser_modifiedAt"))

    with op.batch_alter_table("tokenAuditEvent", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tokenAuditEvent_modifiedAt"))
        batch_op.drop_index(
            "ix_tokenAuditEvent_actionTokenID_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.drop_table("tokenAuditEvent")
    with op.batch_alter_table("actionToken", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_actionToken_modifiedAt"))

    op.drop_table("actionToken")
    op.drop_table("appUser")
