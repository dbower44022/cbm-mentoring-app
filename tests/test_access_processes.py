"""Access-process design gate: grants, sessions, token actions (WTK-002)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from mentorapp.access import (
    DataSourceAccessError,
    IdentityMismatchError,
    InMemoryGrantRegistry,
    InMemorySessionStore,
    InMemoryTokenActionStore,
    ReauthRequiredError,
    SessionEndedError,
    SessionManagement,
    SessionNotFoundError,
    SessionState,
    SourceGrant,
    TokenActionService,
    TokenExhaustedError,
    TokenExpiredError,
    TokenInvalidError,
    TokenRevokedError,
    VerifiedIdentity,
    run_data_source,
)
from mentorapp.storage import AdminSqlError, AdminSqlSource


class FakeClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


def _identity(roles: frozenset[str] = frozenset({"mentor"})) -> VerifiedIdentity:
    return VerifiedIdentity(user_id=uuid.uuid4(), role_names=roles)


# --- DataSourceAccessControl (REQ-006) ---------------------------------------


MENTOR_USER = uuid.uuid4()
PLAIN_SOURCE = AdminSqlSource(
    data_source_key="test.plain", sql_text="SELECT 1 AS answerValue", user_scoped_flag=False
)
SCOPED_SOURCE = AdminSqlSource(
    data_source_key="test.scoped",
    sql_text="SELECT :currentUserID AS scopedUserID",
    user_scoped_flag=True,
)


def test_granted_role_runs_source(session: Session) -> None:
    registry = InMemoryGrantRegistry([SourceGrant("test.plain", "mentor")])
    rows = run_data_source(
        session,
        PLAIN_SOURCE,
        lookup=registry,
        user_id=MENTOR_USER,
        user_roles=frozenset({"mentor", "staff"}),
    )
    assert rows == [{"answerValue": 1}]


def test_source_without_matching_grant_is_refused(session: Session) -> None:
    registry = InMemoryGrantRegistry([SourceGrant("test.plain", "admin")])
    with pytest.raises(DataSourceAccessError) as excinfo:
        run_data_source(
            session,
            PLAIN_SOURCE,
            lookup=registry,
            user_id=MENTOR_USER,
            user_roles=frozenset({"mentor"}),
        )
    assert excinfo.value.data_source_key == "test.plain"


def test_ungranted_source_is_closed_to_everyone(session: Session) -> None:
    # Deny by default: a source nobody has approved runs for nobody.
    with pytest.raises(DataSourceAccessError):
        run_data_source(
            session,
            PLAIN_SOURCE,
            lookup=InMemoryGrantRegistry(),
            user_id=MENTOR_USER,
            user_roles=frozenset({"mentor", "admin", "staff"}),
        )


def test_row_filter_binds_the_session_user(session: Session) -> None:
    registry = InMemoryGrantRegistry([SourceGrant("test.scoped", "mentor")])
    rows = run_data_source(
        session,
        SCOPED_SOURCE,
        lookup=registry,
        user_id=MENTOR_USER,
        user_roles=frozenset({"mentor"}),
    )
    assert rows == [{"scopedUserID": MENTOR_USER.hex}]


def test_row_filter_cannot_be_bypassed_by_caller_params(session: Session) -> None:
    registry = InMemoryGrantRegistry([SourceGrant("test.scoped", "mentor")])
    with pytest.raises(AdminSqlError, match="bound server-side"):
        run_data_source(
            session,
            SCOPED_SOURCE,
            lookup=registry,
            user_id=MENTOR_USER,
            user_roles=frozenset({"mentor"}),
            params={"currentUserID": uuid.uuid4()},
        )


# --- SessionManagement (REQ-005) ----------------------------------------------


@pytest.fixture()
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture()
def sessions(store: InMemorySessionStore, clock: FakeClock) -> SessionManagement:
    return SessionManagement(
        store,
        idle_timeout=timedelta(minutes=30),
        absolute_lifetime=timedelta(hours=12),
        reauth_grace=timedelta(hours=12),
        now=clock.now,
    )


def test_establish_and_resolve(
    sessions: SessionManagement, store: InMemorySessionStore
) -> None:
    identity = _identity()
    reference, record = sessions.establish(identity)
    resolved = sessions.resolve(reference)
    assert resolved.user_id == identity.user_id
    assert resolved.role_names == identity.role_names
    # The browser-held reference is opaque and the store keeps only a hash:
    # neither side of the split yields the other.
    secret = reference.partition(".")[2]
    assert secret.encode("ascii") != store.records[record.session_id].secret_hash


def test_unknown_or_tampered_reference_is_refused(sessions: SessionManagement) -> None:
    reference, _ = sessions.establish(_identity())
    with pytest.raises(SessionNotFoundError):
        sessions.resolve(reference[:-4] + "AAAA")
    with pytest.raises(SessionNotFoundError):
        sessions.resolve("not-a-reference")


def test_idle_expiry_enters_dirty_window_guard(
    sessions: SessionManagement, store: InMemorySessionStore, clock: FakeClock
) -> None:
    reference, record = sessions.establish(_identity())
    clock.advance(timedelta(minutes=31))
    with pytest.raises(ReauthRequiredError) as excinfo:
        sessions.resolve(reference)
    # The session is challenged, not destroyed: the record survives so every
    # window can keep its unsaved work and re-auth in place.
    assert excinfo.value.session_id == record.session_id
    assert store.records[record.session_id].state is SessionState.REAUTH_PENDING


def test_absolute_lifetime_also_expires_an_active_session(
    sessions: SessionManagement, clock: FakeClock
) -> None:
    reference, _ = sessions.establish(_identity())
    # Keep the session non-idle past its absolute lifetime.
    for _ in range(25):
        clock.advance(timedelta(minutes=29))
        try:
            sessions.resolve(reference)
        except ReauthRequiredError:
            return
    pytest.fail("session never hit its absolute lifetime")


def test_one_relogin_restores_all_windows(
    sessions: SessionManagement, clock: FakeClock
) -> None:
    identity = _identity()
    reference, record = sessions.establish(identity)
    clock.advance(timedelta(minutes=31))
    with pytest.raises(ReauthRequiredError):
        sessions.resolve(reference)
    new_reference = sessions.reauthenticate(record.session_id, identity)
    # Same session identity revived: the one new reference serves every window.
    assert sessions.resolve(new_reference).session_id == record.session_id
    # The pre-expiry reference rotated out; a stale window must adopt the new one.
    with pytest.raises(SessionNotFoundError):
        sessions.resolve(reference)


def test_reauth_refuses_a_different_user(
    sessions: SessionManagement, store: InMemorySessionStore, clock: FakeClock
) -> None:
    reference, record = sessions.establish(_identity())
    clock.advance(timedelta(minutes=31))
    with pytest.raises(ReauthRequiredError):
        sessions.resolve(reference)
    with pytest.raises(IdentityMismatchError):
        sessions.reauthenticate(record.session_id, _identity())
    # Refusal leaves the session pending: the right user can still revive it.
    assert store.records[record.session_id].state is SessionState.REAUTH_PENDING


def test_lapsed_reauth_window_ends_the_session(
    sessions: SessionManagement, clock: FakeClock
) -> None:
    identity = _identity()
    reference, record = sessions.establish(identity)
    clock.advance(timedelta(minutes=31))
    with pytest.raises(ReauthRequiredError):
        sessions.resolve(reference)
    clock.advance(timedelta(hours=13))
    with pytest.raises(SessionEndedError):
        sessions.reauthenticate(record.session_id, identity)


def test_logout_ends_access_across_all_windows(sessions: SessionManagement) -> None:
    reference, _ = sessions.establish(_identity())
    window_two_reference = reference  # all windows share the one session reference
    sessions.logout(reference)
    with pytest.raises(SessionEndedError):
        sessions.resolve(window_two_reference)


# --- TokenAction (REQ-007) ------------------------------------------------------


@pytest.fixture()
def token_store() -> InMemoryTokenActionStore:
    return InMemoryTokenActionStore()


@pytest.fixture()
def tokens(token_store: InMemoryTokenActionStore, clock: FakeClock) -> TokenActionService:
    return TokenActionService(token_store, signing_key=b"test-signing-key", now=clock.now)


def _mint(tokens: TokenActionService, clock: FakeClock, **overrides: object) -> str:
    kwargs: dict = {
        "user_id": MENTOR_USER,
        "action_name": "acceptAssignment",
        "expires_at": clock.now() + timedelta(days=3),
    }
    kwargs.update(overrides)
    return tokens.mint(**kwargs)


def test_mint_redeem_and_audit_trail(
    tokens: TokenActionService, token_store: InMemoryTokenActionStore, clock: FakeClock
) -> None:
    token = _mint(tokens, clock)
    record = tokens.redeem(token, expected_action="acceptAssignment")
    assert record.user_id == MENTOR_USER
    assert record.use_count == 1
    assert [e.event_name for e in token_store.audit] == ["minted", "redeemed"]


def test_single_use_token_exhausts(tokens: TokenActionService, clock: FakeClock) -> None:
    token = _mint(tokens, clock)
    tokens.redeem(token, expected_action="acceptAssignment")
    with pytest.raises(TokenExhaustedError):
        tokens.redeem(token, expected_action="acceptAssignment")


def test_use_budget_is_accounted_per_redemption(
    tokens: TokenActionService, clock: FakeClock
) -> None:
    token = _mint(tokens, clock, max_uses=3)
    for _ in range(3):
        tokens.redeem(token, expected_action="acceptAssignment")
    with pytest.raises(TokenExhaustedError):
        tokens.redeem(token, expected_action="acceptAssignment")


def test_expired_token_is_refused(tokens: TokenActionService, clock: FakeClock) -> None:
    token = _mint(tokens, clock)
    clock.advance(timedelta(days=4))
    with pytest.raises(TokenExpiredError):
        tokens.redeem(token, expected_action="acceptAssignment")


def test_revoked_token_is_refused_and_audited(
    tokens: TokenActionService, token_store: InMemoryTokenActionStore, clock: FakeClock
) -> None:
    token = _mint(tokens, clock)
    token_action_id = uuid.UUID(hex=token.partition(".")[0])
    tokens.revoke(token_action_id)
    with pytest.raises(TokenRevokedError):
        tokens.redeem(token, expected_action="acceptAssignment")
    assert [e.event_name for e in token_store.audit] == ["minted", "revoked"]


def test_token_is_bound_to_its_one_action(tokens: TokenActionService, clock: FakeClock) -> None:
    token = _mint(tokens, clock)
    with pytest.raises(TokenInvalidError):
        tokens.redeem(token, expected_action="resetPassword")


def test_tampered_or_forged_token_is_refused(
    tokens: TokenActionService, token_store: InMemoryTokenActionStore, clock: FakeClock
) -> None:
    token = _mint(tokens, clock)
    with pytest.raises(TokenInvalidError):
        tokens.validate(token[:-4] + "AAAA", expected_action="acceptAssignment")
    forged = TokenActionService(token_store, signing_key=b"other-key", now=clock.now)
    with pytest.raises(TokenInvalidError):
        forged.validate(token, expected_action="acceptAssignment")
    with pytest.raises(TokenInvalidError):
        tokens.validate("junk", expected_action="acceptAssignment")
