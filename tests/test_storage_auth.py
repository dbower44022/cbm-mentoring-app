"""Auth-entity model tests (WTK-001): users, sessions, tokens, and grants.

The generic structural/key-policy tests in ``test_storage_models`` and the
migration-parity tests in ``test_storage_migrations`` sweep these tables
automatically; this module covers the auth-specific behavior — session and
grant integrity, the reconciled session/token column shapes (WTK-002 is the
process authority), the token audit trail, and the soft-delete re-issue
semantics revocation relies on.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mentorapp.storage import (
    AccessGrant,
    ActionToken,
    AppUser,
    AuthSession,
    DataSource,
    DataSourceRoleGrant,
    TokenAuditEvent,
    utcnow,
)


def _user(session: Session, username: str = "dmentor") -> AppUser:
    user = AppUser(crm_user_id=f"CRM-{username}", username=username)
    session.add(user)
    session.flush()
    return user


def _session_row(user: AppUser, secret_hash: str = "ab" * 32) -> AuthSession:
    return AuthSession(
        user_id=user.user_id,
        session_secret_hash=secret_hash,
        session_expires_at=utcnow() + timedelta(hours=8),
    )


def test_auth_session_requires_existing_user(session: Session) -> None:
    orphan = _session_row(AppUser(user_id=uuid.uuid4(), crm_user_id="x", username="x"))
    session.add(orphan)
    with pytest.raises(IntegrityError):
        session.commit()


def test_session_secret_hash_is_deliberately_not_unique(session: Session) -> None:
    # Lookup is by the primary key (the sessionID half of the reference), so
    # the hash needs no index and no uniqueness — the store holds only the
    # SHA-256 of the secret, never anything a leaked table could replay.
    user = _user(session)
    session.add_all([_session_row(user, "cd" * 32), _session_row(user, "cd" * 32)])
    session.commit()


def test_session_defaults_match_the_process_vocabulary(session: Session) -> None:
    user = _user(session)
    row = _session_row(user)
    session.add(row)
    session.commit()
    # A fresh session is active with no pending grace deadline; roles and the
    # encrypted CRM credential arrive from the login exchange, not defaults.
    assert row.session_state == "active"
    assert row.session_reauth_deadline is None
    assert row.session_last_seen_at is not None
    assert row.session_role_names is None
    assert row.crm_credential_encrypted is None


def test_session_reauth_pending_state_is_persistable(session: Session) -> None:
    # The REQ-005 dirty-window guard: REAUTH_PENDING must survive as row
    # state, with its grace deadline, so one re-login can revive all windows.
    user = _user(session)
    row = _session_row(user)
    row.session_state = "reauthPending"
    row.session_reauth_deadline = utcnow() + timedelta(hours=12)
    session.add(row)
    session.commit()
    assert row.session_state == "reauthPending"
    assert row.session_reauth_deadline is not None


def _token(**overrides: object) -> ActionToken:
    kwargs: dict = {
        "token_action": "magicLink",
        "token_identity": "mentor@example.org",
        "token_expires_at": utcnow() + timedelta(hours=1),
    }
    kwargs.update(overrides)
    return ActionToken(**kwargs)


def test_action_token_starts_unused_single_use_and_unrevoked(session: Session) -> None:
    token = _token()
    session.add(token)
    session.commit()
    # No signature column exists: the HMAC is recomputed from the server key
    # on every presentation and lookup is by primary key (WTK-002).
    assert token.token_use_count == 0
    assert token.token_max_uses == 1
    assert token.token_revoked_at is None
    assert token.user_id is None


def test_action_token_binds_a_user_only_when_one_exists(session: Session) -> None:
    user = _user(session)
    session.add(_token(user_id=user.user_id))
    session.commit()

    session.add(_token(user_id=uuid.uuid4()))
    with pytest.raises(IntegrityError):
        session.commit()


def test_token_audit_events_append_one_row_per_lifecycle_moment(session: Session) -> None:
    # The storage shape of the WTK-002 audit contract: every mint, redeem,
    # and revoke appends one row keyed to the token.
    token = _token()
    session.add(token)
    session.flush()
    for event_name in ("minted", "redeemed", "revoked"):
        session.add(
            TokenAuditEvent(
                action_token_id=token.action_token_id,
                token_event_name=event_name,
                token_event_occurred_at=utcnow(),
            )
        )
    session.commit()

    orphan = TokenAuditEvent(
        action_token_id=uuid.uuid4(),
        token_event_name="minted",
        token_event_occurred_at=utcnow(),
    )
    session.add(orphan)
    with pytest.raises(IntegrityError):
        session.commit()


def test_access_grant_unique_per_user_and_reissuable_after_revoke(session: Session) -> None:
    user = _user(session)
    grant = AccessGrant(user_id=user.user_id, access_grant_key="adminSql.author")
    session.add(grant)
    session.commit()

    session.add(AccessGrant(user_id=user.user_id, access_grant_key="adminSql.author"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Revocation is a soft delete; the same grant can be issued again later.
    grant = session.get_one(AccessGrant, grant.access_grant_id)
    grant.deleted_at = utcnow()
    session.add(AccessGrant(user_id=user.user_id, access_grant_key="adminSql.author"))
    session.commit()


def _source(session: Session) -> DataSource:
    source = DataSource(
        data_source_key="mentorRoster",
        data_source_name="Mentor Roster",
        data_source_sql='SELECT * FROM "mentorView"',
        user_row_filter="mentorUserID",
    )
    session.add(source)
    session.flush()
    return source


def test_data_source_role_grant_is_unique_per_source_and_role(session: Session) -> None:
    # The read-surface standard keys the run permission by STAFF ROLE; one
    # live row per (source, role) is the whole security boundary.
    source = _source(session)
    session.add(DataSourceRoleGrant(data_source_id=source.data_source_id, role_name="mentor"))
    session.commit()

    session.add(DataSourceRoleGrant(data_source_id=source.data_source_id, role_name="mentor"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # A different role on the same source is a different approval.
    session.add(DataSourceRoleGrant(data_source_id=source.data_source_id, role_name="admin"))
    session.commit()


def test_data_source_role_grant_is_reissuable_after_revoke(session: Session) -> None:
    source = _source(session)
    grant = DataSourceRoleGrant(data_source_id=source.data_source_id, role_name="mentor")
    session.add(grant)
    session.commit()

    # Revoking is a soft delete (the grant history survives); the live-row
    # predicate lets the same approval be granted again later.
    grant.deleted_at = utcnow()
    session.add(DataSourceRoleGrant(data_source_id=source.data_source_id, role_name="mentor"))
    session.commit()


def test_data_source_is_not_user_scoped_by_default(session: Session) -> None:
    source = DataSource(
        data_source_key="orgSummary",
        data_source_name="Org Summary",
        data_source_sql='SELECT * FROM "engagementView"',
    )
    session.add(source)
    session.commit()
    # Null userRowFilter = not user-scoped (DB-S9 declaration lives on the row).
    assert source.user_row_filter is None
