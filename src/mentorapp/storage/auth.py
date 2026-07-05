"""Auth and access entities: users, sessions, action tokens, and data-source grants.

Persistence model for authentication and authorization (WTK-001). ``appUser``
is the app-local identity anchored to the CRM system of record via
``crmUserID``; ``authSession`` is the server-side session behind the opaque
cookie reference; ``actionToken`` is the single-purpose signed token (invite,
password reset, magic link) with a use counter; ``accessGrant`` names a user's
app-level capabilities; ``dataSource`` persists the admin-authored SQL sources
that ``mentorapp.storage.adminsql`` executes, including the DB-S9 user-row
scoping declaration (``userRowFilter``).

Associations: ``userCrmAccount`` maps a user to the CRM accounts they act
over (CRM identifiers are soft references — the CRM is the system of record,
so there is nothing local to foreign-key); ``userDataSourceGrant`` is the
per-source run permission the read-surface standard names as the security
boundary for admin SQL.

These are platform tables (``StructuralColumnsMixin`` + ``Base``, not
``BaseEntity``): like the registry, jobs, and the feed they get no registry
rows and no generated read views — sessions, tokens, and grants must never be
reachable through the admin read surface.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from mentorapp.storage.base import Base, StructuralColumnsMixin, utcnow, uuid7

# Same partial live-row predicate as models.py (DB-S3): unique constraints and
# lookup indexes never pay for soft-deleted rows or collide with corpses.
_LIVE = text('"deletedAt" IS NULL')


class AppUser(StructuralColumnsMixin, Base):
    """One app-local user identity, anchored to the CRM system of record.

    The table is ``appUser`` because ``user`` is a reserved word on Postgres;
    the key stays ``userID`` so the system-wide userID vocabulary already used
    by ``userPreference.userID`` and the audit columns keeps one meaning
    (DB-R2). ``crmUserID`` is the CRM's own identifier for this person — a
    soft reference stored as text because the CRM owns its format.
    """

    __tablename__ = "appUser"
    __table_args__ = (
        Index(
            "uq_appUser_username_live",
            "username",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
        Index(
            "uq_appUser_crmUserID_live",
            "crmUserID",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column("userID", primary_key=True, default=uuid7)
    crm_user_id: Mapped[str] = mapped_column("crmUserID", String(100), nullable=False)
    username: Mapped[str] = mapped_column("username", String(200), nullable=False)


class AuthSession(StructuralColumnsMixin, Base):
    """One server-side login session behind an opaque client-held reference.

    The client holds only ``sessionOpaqueReference`` — a random lookup value
    with no derivable meaning; the row is the session state. A session ends
    three ways: the absolute cap ``sessionExpiresAt`` passes, it idles past
    ``sessionIdleTimeoutSeconds`` since ``sessionLastSeenAt``, or it is
    revoked (``sessionRevokedFlag`` — an explicit flag, not a soft delete, so
    revocation is queryable state distinct from record lifecycle).
    """

    __tablename__ = "authSession"
    __table_args__ = (
        # The per-request lookup: cookie reference → session row.
        Index(
            "uq_authSession_opaqueReference_live",
            "sessionOpaqueReference",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
        # The revocation sweep: every session of one user (logout-everywhere).
        Index(
            "ix_authSession_userID_live",
            "userID",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    auth_session_id: Mapped[uuid.UUID] = mapped_column(
        "authSessionID", primary_key=True, default=uuid7
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        "userID", ForeignKey("appUser.userID"), nullable=False
    )
    session_opaque_reference: Mapped[str] = mapped_column(
        "sessionOpaqueReference", String(200), nullable=False
    )
    session_expires_at: Mapped[datetime] = mapped_column(
        "sessionExpiresAt", DateTime(timezone=True), nullable=False
    )
    session_idle_timeout_seconds: Mapped[int] = mapped_column(
        "sessionIdleTimeoutSeconds", nullable=False
    )
    # Idle timeout is only enforceable against a last-activity stamp; the API
    # refreshes this on every authenticated request.
    session_last_seen_at: Mapped[datetime] = mapped_column(
        "sessionLastSeenAt", DateTime(timezone=True), nullable=False, default=utcnow
    )
    session_revoked_flag: Mapped[bool] = mapped_column(
        "sessionRevokedFlag", nullable=False, default=False
    )


class ActionToken(StructuralColumnsMixin, Base):
    """One single-purpose signed token: invite, reset, verification, magic link.

    ``tokenIdentity`` is who the token asserts (typically an email address —
    it can predate any ``appUser`` row, so it is a value, not a foreign key).
    ``tokenSignature`` is the server-computed MAC the presented token must
    match; ``tokenUseCount`` counts redemptions so single-use enforcement is
    an increment-and-check, never a delete (the row is the audit trail).
    """

    __tablename__ = "actionToken"
    __table_args__ = (
        # The redemption lookup: presented token → row, live rows only.
        Index(
            "uq_actionToken_signature_live",
            "tokenSignature",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    action_token_id: Mapped[uuid.UUID] = mapped_column(
        "actionTokenID", primary_key=True, default=uuid7
    )
    token_action: Mapped[str] = mapped_column("tokenAction", String(100), nullable=False)
    # 320 covers the maximal RFC email shape, the common identity here.
    token_identity: Mapped[str] = mapped_column("tokenIdentity", String(320), nullable=False)
    token_expires_at: Mapped[datetime] = mapped_column(
        "tokenExpiresAt", DateTime(timezone=True), nullable=False
    )
    token_signature: Mapped[str] = mapped_column("tokenSignature", String(500), nullable=False)
    token_use_count: Mapped[int] = mapped_column("tokenUseCount", nullable=False, default=0)


class AccessGrant(StructuralColumnsMixin, Base):
    """One named app-level capability held by one user.

    ``accessGrantKey`` is an app-validated vocabulary (e.g. ``adminSql.author``)
    — data, never a database enum (DB-S7). Revoking is a soft delete: the
    partial unique index lets the same grant be re-issued later.
    """

    __tablename__ = "accessGrant"
    __table_args__ = (
        Index(
            "uq_accessGrant_user_key_live",
            "userID",
            "accessGrantKey",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    access_grant_id: Mapped[uuid.UUID] = mapped_column(
        "accessGrantID", primary_key=True, default=uuid7
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        "userID", ForeignKey("appUser.userID"), nullable=False
    )
    access_grant_key: Mapped[str] = mapped_column("accessGrantKey", String(100), nullable=False)


class DataSource(StructuralColumnsMixin, Base):
    """One persisted admin-authored SQL data source (DB-S9).

    The stored form behind ``adminsql.AdminSqlSource``: ``dataSourceKey`` is
    the stable key other records reference (e.g.
    ``workprocessRegistration.targetDataSourceKeys``), ``dataSourceSql`` the
    SELECT executed over the read views under the read-only role. A non-null
    ``userRowFilter`` declares the source user-scoped and names the view
    column bound server-side to ``:currentUserID`` — the author references
    the scoping but can neither supply nor bypass it; null means the source
    is not user-scoped.
    """

    __tablename__ = "dataSource"
    __table_args__ = (
        Index(
            "uq_dataSource_dataSourceKey_live",
            "dataSourceKey",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    data_source_id: Mapped[uuid.UUID] = mapped_column(
        "dataSourceID", primary_key=True, default=uuid7
    )
    data_source_key: Mapped[str] = mapped_column("dataSourceKey", String(200), nullable=False)
    data_source_name: Mapped[str] = mapped_column("dataSourceName", String(200), nullable=False)
    # Text, not a bounded String: SQL bodies are genuinely unbounded prose and
    # truncation would corrupt a query.
    data_source_sql: Mapped[str] = mapped_column("dataSourceSql", Text(), nullable=False)
    user_row_filter: Mapped[str | None] = mapped_column(
        "userRowFilter", String(200), default=None
    )


class UserCrmAccount(StructuralColumnsMixin, Base):
    """The user ↔ CRM-account association: which CRM accounts a user acts over.

    ``crmAccountID`` is the CRM's identifier, stored as text like
    ``appUser.crmUserID`` — the CRM is the system of record, so there is no
    local row to foreign-key.
    """

    __tablename__ = "userCrmAccount"
    __table_args__ = (
        Index(
            "uq_userCrmAccount_user_account_live",
            "userID",
            "crmAccountID",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    user_crm_account_id: Mapped[uuid.UUID] = mapped_column(
        "userCrmAccountID", primary_key=True, default=uuid7
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        "userID", ForeignKey("appUser.userID"), nullable=False
    )
    crm_account_id: Mapped[str] = mapped_column("crmAccountID", String(100), nullable=False)


class UserDataSourceGrant(StructuralColumnsMixin, Base):
    """The user ↔ data-source run permission — admin SQL's security boundary.

    The read-surface standard places per-source grants in app tables; a live
    row here is what lets a user run that source. Revoking is a soft delete,
    so the grant history survives.
    """

    __tablename__ = "userDataSourceGrant"
    __table_args__ = (
        Index(
            "uq_userDataSourceGrant_user_source_live",
            "userID",
            "dataSourceID",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
        # The admin read "who may run this source"; the unique above leads
        # with userID so it cannot serve this scan.
        Index(
            "ix_userDataSourceGrant_dataSourceID_live",
            "dataSourceID",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    user_data_source_grant_id: Mapped[uuid.UUID] = mapped_column(
        "userDataSourceGrantID", primary_key=True, default=uuid7
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        "userID", ForeignKey("appUser.userID"), nullable=False
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        "dataSourceID", ForeignKey("dataSource.dataSourceID"), nullable=False
    )
