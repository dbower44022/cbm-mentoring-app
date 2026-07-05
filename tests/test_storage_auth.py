"""Auth-entity model tests (WTK-001): users, sessions, tokens, and grants.

The generic structural/key-policy tests in ``test_storage_models`` and the
migration-parity tests in ``test_storage_migrations`` sweep these tables
automatically; this module covers the auth-specific behavior — session and
grant integrity against ``appUser``, live-row uniqueness of the security
lookups, and the soft-delete re-issue semantics revocation relies on.
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
    UserDataSourceGrant,
    utcnow,
)


def _user(session: Session, username: str = "dmentor") -> AppUser:
    user = AppUser(crm_user_id=f"CRM-{username}", username=username)
    session.add(user)
    session.flush()
    return user


def _session_row(user: AppUser, reference: str) -> AuthSession:
    return AuthSession(
        user_id=user.user_id,
        session_opaque_reference=reference,
        session_expires_at=utcnow() + timedelta(hours=8),
        session_idle_timeout_seconds=1800,
    )


def test_auth_session_requires_existing_user(session: Session) -> None:
    orphan = _session_row(AppUser(user_id=uuid.uuid4(), crm_user_id="x", username="x"), "ref")
    session.add(orphan)
    with pytest.raises(IntegrityError):
        session.commit()


def test_opaque_reference_unique_across_live_rows_only(session: Session) -> None:
    user = _user(session)
    corpse = _session_row(user, "ref-1")
    corpse.deleted_at = utcnow()
    session.add_all([corpse, _session_row(user, "ref-1")])
    session.commit()

    session.add(_session_row(user, "ref-1"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_session_defaults_support_idle_and_revocation(session: Session) -> None:
    user = _user(session)
    row = _session_row(user, "ref-2")
    session.add(row)
    session.commit()
    # Idle timeout is enforced against lastSeenAt; revocation starts false.
    assert row.session_last_seen_at is not None
    assert row.session_revoked_flag is False


def test_action_token_starts_unused_and_signature_is_unique(session: Session) -> None:
    def token() -> ActionToken:
        return ActionToken(
            token_action="passwordReset",
            token_identity="mentor@example.org",
            token_expires_at=utcnow() + timedelta(hours=1),
            token_signature="sig-abc",
        )

    first = token()
    session.add(first)
    session.commit()
    assert first.token_use_count == 0

    session.add(token())
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


def test_data_source_grant_ties_user_to_persisted_source(session: Session) -> None:
    user = _user(session)
    source = DataSource(
        data_source_key="mentorRoster",
        data_source_name="Mentor Roster",
        data_source_sql='SELECT * FROM "mentorView"',
        user_row_filter="mentorUserID",
    )
    session.add(source)
    session.flush()
    session.add(UserDataSourceGrant(user_id=user.user_id, data_source_id=source.data_source_id))
    session.commit()

    session.add(UserDataSourceGrant(user_id=user.user_id, data_source_id=source.data_source_id))
    with pytest.raises(IntegrityError):
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
