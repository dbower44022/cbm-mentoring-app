"""Auth and access entities: users, sessions, action tokens, and data-source grants.

Persistence model for authentication and authorization (WTK-001, reconciled
to the WTK-002/WTK-003 processes). ``appUser`` is the app-local identity
anchored to the CRM system of record via ``crmUserID``; ``authSession`` is
the persisted form of the SessionManagement record (``access/sessions.py``)
behind the opaque cookie reference; ``actionToken`` is the single-purpose
token (invite, magic link) with a use budget, ``tokenAuditEvent`` its
append-only mint/redeem/revoke trail; ``accessGrant`` names a user's
app-level capabilities; ``dataSource`` persists the admin-authored SQL
sources that ``mentorapp.storage.adminsql`` executes, including the DB-S9
user-row scoping declaration (``userRowFilter``).

Associations: ``userCrmAccount`` maps a user to the CRM accounts they act
over (CRM identifiers are soft references — the CRM is the system of record,
so there is nothing local to foreign-key); ``dataSourceRoleGrant`` is the
per-source, role-keyed run permission the read-surface standard names as the
security boundary for admin SQL.

These are platform tables (``StructuralColumnsMixin`` + ``Base``, not
``BaseEntity``): like the registry, jobs, and the feed they get no registry
rows and no generated read views — sessions, tokens, and grants must never be
reachable through the admin read surface.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from mentorapp.storage.base import Base, JsonValue, StructuralColumnsMixin, utcnow, uuid7

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
    """One server-side login session — the persisted SessionManagement record.

    ``authSessionID`` IS the process's ``session_id``: the client-visible half
    of the reference ``<sessionID hex>.<secret>`` (``access/sessions.py``,
    WTK-002 — the authority on reference handling). Lookup is by primary key,
    never by a stored reference — the row keeps only ``sessionSecretHash``,
    so a leaked table yields no usable references. Idle timeout is
    SessionManagement service configuration, not per-row state; the row
    carries only what must survive the process (state, deadlines, roles, and
    the encrypted CRM credential).
    """

    __tablename__ = "authSession"
    __table_args__ = (
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
    # SHA-256 hex of the reference secret; the store never holds the raw
    # secret. Not unique — the secret is 256 random bits and lookup is by PK.
    session_secret_hash: Mapped[str] = mapped_column(
        "sessionSecretHash", String(64), nullable=False
    )
    # Vocabulary active/reauthPending/ended — data validated by the process,
    # not a DB enum (DB-S7). The REQ-005 dirty-window guard and one-relogin-
    # restores-all-windows require the revivable REAUTH_PENDING state to be
    # persistent, which is why this is a state, not a revoked flag.
    session_state: Mapped[str] = mapped_column(
        "sessionState", String(20), nullable=False, default="active"
    )
    session_expires_at: Mapped[datetime] = mapped_column(
        "sessionExpiresAt", DateTime(timezone=True), nullable=False
    )
    # Non-null only in reauthPending: the grace deadline after which the
    # session is unrevivable and ends.
    session_reauth_deadline: Mapped[datetime | None] = mapped_column(
        "sessionReauthDeadline", DateTime(timezone=True), default=None
    )
    # Idle timeout is only enforceable against a last-activity stamp; the API
    # refreshes this on every authenticated request.
    session_last_seen_at: Mapped[datetime] = mapped_column(
        "sessionLastSeenAt", DateTime(timezone=True), nullable=False, default=utcnow
    )
    # Role names captured at login. Roles are session-scoped, refreshed at
    # each login/reauth — there is no persistent user-role table; the CRM is
    # the role source.
    session_role_names: Mapped[list[str] | None] = mapped_column(
        "sessionRoleNames", JsonValue, default=None
    )
    # The CRM-issued act-as-user token, AEAD-encrypted under a server key
    # (same key-management family as the token signing key); never plaintext,
    # never in any read view; null once the session ends (the WTK-003/FND-006
    # custody decision: the session owns the credential).
    crm_credential_encrypted: Mapped[str | None] = mapped_column(
        "crmCredentialEncrypted", Text(), default=None
    )


class ActionToken(StructuralColumnsMixin, Base):
    """One single-purpose signed token: invite, verification, magic link.

    ``tokenIdentity`` is who the token asserts (WTK-001 is the authority
    here: email-shaped, it may predate any ``appUser`` row, so it is a value,
    not a foreign key); ``userID`` is bound only when the identity already
    resolves to a user (e.g. a password-less magic link for an existing
    account). No signature is stored: the HMAC is recomputed from the server
    signing key on every presentation and lookup is by the primary key
    ``actionTokenID`` (WTK-002 ``tokens.py`` is the authority). Use budget
    and revocation live on the row (``tokenMaxUses``/``tokenUseCount``,
    ``tokenRevokedAt``) so enforcement is an increment-and-check, never a
    delete.
    """

    __tablename__ = "actionToken"

    action_token_id: Mapped[uuid.UUID] = mapped_column(
        "actionTokenID", primary_key=True, default=uuid7
    )
    token_action: Mapped[str] = mapped_column("tokenAction", String(100), nullable=False)
    # 320 covers the maximal RFC email shape, the common identity here.
    token_identity: Mapped[str] = mapped_column("tokenIdentity", String(320), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        "userID", ForeignKey("appUser.userID"), default=None
    )
    token_expires_at: Mapped[datetime] = mapped_column(
        "tokenExpiresAt", DateTime(timezone=True), nullable=False
    )
    token_max_uses: Mapped[int] = mapped_column("tokenMaxUses", nullable=False, default=1)
    token_use_count: Mapped[int] = mapped_column("tokenUseCount", nullable=False, default=0)
    token_revoked_at: Mapped[datetime | None] = mapped_column(
        "tokenRevokedAt", DateTime(timezone=True), default=None
    )


class TokenAuditEvent(StructuralColumnsMixin, Base):
    """One accountable moment in a token's life: minted, redeemed, or revoked.

    Append-only: every mint, redemption, and revocation appends exactly one
    row — the audit trail is part of the token contract (WTK-002), which
    supersedes WTK-001's "the row is the audit trail". Rows are never
    updated or deleted; the token's current state lives on ``actionToken``,
    its history lives here.
    """

    __tablename__ = "tokenAuditEvent"
    __table_args__ = (
        # The trail read: every event of one token, in insertion order.
        Index(
            "ix_tokenAuditEvent_actionTokenID_live",
            "actionTokenID",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    token_audit_event_id: Mapped[uuid.UUID] = mapped_column(
        "tokenAuditEventID", primary_key=True, default=uuid7
    )
    action_token_id: Mapped[uuid.UUID] = mapped_column(
        "actionTokenID", ForeignKey("actionToken.actionTokenID"), nullable=False
    )
    # minted/redeemed/revoked — process vocabulary, not a DB enum (DB-S7).
    token_event_name: Mapped[str] = mapped_column("tokenEventName", String(50), nullable=False)
    token_event_occurred_at: Mapped[datetime] = mapped_column(
        "tokenEventOccurredAt", DateTime(timezone=True), nullable=False
    )


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
    # REQ-019 authoring modes (WTK-041): null = authored as raw SQL; non-null
    # = the visual-builder document (joins, filters, calculated fields) that
    # compiles into dataSourceSql — the SQL stays the one executable form.
    visual_query_definition: Mapped[dict[str, Any] | None] = mapped_column(
        "visualQueryDefinition", JsonValue, default=None
    )
    # The fields the source exposes — the bound on what its views may display
    # and sort (REQ-019); the API validates gridView writes against this list.
    exposed_fields: Mapped[list[str]] = mapped_column(
        "exposedFields", JsonValue, nullable=False, default=list
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


class DataSourceRoleGrant(StructuralColumnsMixin, Base):
    """The role ↔ data-source run permission — admin SQL's security boundary.

    The read-surface standard (SKL-120/DB-S9) keys per-source grants by STAFF
    ROLE — "which staff roles may run a data source... that is the security
    boundary" — matching ``access/grants.py`` ``SourceGrant``/``GrantLookup``.
    A user's roles come from the session (captured from the CRM at login), so
    a live row here is what lets any holder of ``roleName`` run the source;
    deny-by-default means a source with no grant rows is closed to everyone.
    Revoking is a soft delete, so the grant history survives.
    """

    __tablename__ = "dataSourceRoleGrant"
    __table_args__ = (
        Index(
            "uq_dataSourceRoleGrant_source_role_live",
            "dataSourceID",
            "roleName",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
        # The admin read "what may this role run"; the unique above leads
        # with dataSourceID so it cannot serve this scan.
        Index(
            "ix_dataSourceRoleGrant_roleName_live",
            "roleName",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    data_source_role_grant_id: Mapped[uuid.UUID] = mapped_column(
        "dataSourceRoleGrantID", primary_key=True, default=uuid7
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        "dataSourceID", ForeignKey("dataSource.dataSourceID"), nullable=False
    )
    role_name: Mapped[str] = mapped_column("roleName", String(100), nullable=False)
