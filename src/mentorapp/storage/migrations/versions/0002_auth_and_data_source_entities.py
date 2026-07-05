"""Auth and data-source entities (WTK-001).

Creates the auth/access platform tables: ``appUser``, ``authSession``,
``actionToken``, ``accessGrant``, ``dataSource``, and the ``userCrmAccount``
and ``userDataSourceGrant`` associations. Same structural rules as 0001:
UUIDv7 app-generated keys (REQ-047), the eight structural system columns
(REQ-053), and partial live-row indexes (REQ-052). These are platform tables
— no schema-registry rows and no read views, so sessions, tokens, and grants
never surface through the admin read surface.

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
        "actionToken",
        sa.Column("actionTokenID", sa.Uuid(), nullable=False),
        sa.Column("tokenAction", sa.String(length=100), nullable=False),
        sa.Column("tokenIdentity", sa.String(length=320), nullable=False),
        sa.Column("tokenExpiresAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tokenSignature", sa.String(length=500), nullable=False),
        sa.Column("tokenUseCount", sa.Integer(), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("actionTokenID", name=op.f("pk_actionToken")),
    )
    with op.batch_alter_table("actionToken", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_actionToken_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_actionToken_signature_live",
            ["tokenSignature"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

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
        sa.Column("sessionOpaqueReference", sa.String(length=200), nullable=False),
        sa.Column("sessionExpiresAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sessionIdleTimeoutSeconds", sa.Integer(), nullable=False),
        sa.Column("sessionLastSeenAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sessionRevokedFlag", sa.Boolean(), nullable=False),
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
        batch_op.create_index(
            "uq_authSession_opaqueReference_live",
            ["sessionOpaqueReference"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
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
        "userDataSourceGrant",
        sa.Column("userDataSourceGrantID", sa.Uuid(), nullable=False),
        sa.Column("userID", sa.Uuid(), nullable=False),
        sa.Column("dataSourceID", sa.Uuid(), nullable=False),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["dataSourceID"],
            ["dataSource.dataSourceID"],
            name=op.f("fk_userDataSourceGrant_dataSourceID_dataSource"),
        ),
        sa.ForeignKeyConstraint(
            ["userID"], ["appUser.userID"], name=op.f("fk_userDataSourceGrant_userID_appUser")
        ),
        sa.PrimaryKeyConstraint("userDataSourceGrantID", name=op.f("pk_userDataSourceGrant")),
    )
    with op.batch_alter_table("userDataSourceGrant", schema=None) as batch_op:
        batch_op.create_index(
            "ix_userDataSourceGrant_dataSourceID_live",
            ["dataSourceID"],
            unique=False,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.create_index(
            batch_op.f("ix_userDataSourceGrant_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_userDataSourceGrant_user_source_live",
            ["userID", "dataSourceID"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )


def downgrade() -> None:
    with op.batch_alter_table("userDataSourceGrant", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_userDataSourceGrant_user_source_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_userDataSourceGrant_modifiedAt"))
        batch_op.drop_index(
            "ix_userDataSourceGrant_dataSourceID_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.drop_table("userDataSourceGrant")
    with op.batch_alter_table("userCrmAccount", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_userCrmAccount_user_account_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_userCrmAccount_modifiedAt"))

    op.drop_table("userCrmAccount")
    with op.batch_alter_table("authSession", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_authSession_opaqueReference_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
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

    op.drop_table("appUser")
    with op.batch_alter_table("actionToken", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_actionToken_signature_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_actionToken_modifiedAt"))

    op.drop_table("actionToken")
