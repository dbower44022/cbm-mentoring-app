"""Stored auth adapters (WTK-191): sessions, tokens, and the identity bridge.

Parity is asserted THROUGH the processes — ``SessionManagement`` and
``TokenActionService`` drive the stored adapters exactly as the design-gate
suites drive the in-memory references — and the FND-006 custody decision is
asserted at the column: the CRM token is sealed at rest, never plaintext,
and gone once the session ends. ``expire_all`` between steps forces every
read back through the database, so identity-map reuse can't fake a
round-trip.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from mentorapp.access import (
    CredentialCipher,
    CredentialSealError,
    ReauthRequiredError,
    SessionEndedError,
    SessionManagement,
    SessionNotFoundError,
    SessionState,
    StoredIdentityBridge,
    StoredSessionStore,
    StoredTokenActionStore,
    TokenActionService,
    TokenExhaustedError,
    VerifiedIdentity,
)
from mentorapp.crm.auth import CrmUserCredential, CrmVerifiedIdentity
from mentorapp.storage import ActionToken, AppUser, TokenAuditEvent, uuid7

CREDENTIAL_KEY = b"k" * 32
CRM_SECRET = "espo-issued-token-0451"


class Clock:
    """Controllable now() so expiry is a test decision, not a sleep."""

    def __init__(self) -> None:
        self.current = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def user(session: Session) -> AppUser:
    row = AppUser(crm_user_id="crm-77", username="mentor.jane")
    session.add(row)
    session.flush()
    return row


@pytest.fixture()
def store(session: Session) -> StoredSessionStore:
    return StoredSessionStore(session, cipher=CredentialCipher(CREDENTIAL_KEY))


def _identity(user: AppUser) -> VerifiedIdentity:
    return VerifiedIdentity(
        user_id=user.user_id,
        role_names=frozenset({"mentor", "staff"}),
        crm_credential=CrmUserCredential(username="mentor.jane", secret=CRM_SECRET),
    )


def _raw_credential_column(session: Session) -> str | None:
    return session.execute(
        text('SELECT "crmCredentialEncrypted" FROM "authSession"')
    ).scalar_one()


class TestStoredSessionStore:
    def test_round_trip_preserves_every_field(
        self, session: Session, store: StoredSessionStore, user: AppUser, clock: Clock
    ) -> None:
        management = SessionManagement(store, now=clock)
        _reference, record = management.establish(_identity(user))
        session.expire_all()
        assert store.load(record.session_id) == record

    def test_missing_session_loads_none(self, store: StoredSessionStore) -> None:
        assert store.load(uuid7()) is None

    def test_credential_is_sealed_at_rest(
        self, session: Session, store: StoredSessionStore, user: AppUser, clock: Clock
    ) -> None:
        management = SessionManagement(store, now=clock)
        management.establish(_identity(user))
        sealed = _raw_credential_column(session)
        assert sealed is not None
        assert CRM_SECRET not in sealed
        assert "mentor.jane" not in sealed

    def test_ended_session_drops_the_credential(
        self, session: Session, store: StoredSessionStore, user: AppUser, clock: Clock
    ) -> None:
        management = SessionManagement(store, now=clock)
        reference, record = management.establish(_identity(user))
        management.logout(reference)
        assert _raw_credential_column(session) is None
        session.expire_all()
        loaded = store.load(record.session_id)
        assert loaded is not None
        assert loaded.crm_credential is None
        assert loaded.state is SessionState.ENDED

    def test_lifecycle_parity_with_the_reference_store(
        self, session: Session, store: StoredSessionStore, user: AppUser, clock: Clock
    ) -> None:
        # The design-gate flow: expire to reauth-pending, revive in place with
        # a rotated secret, then logout — every step re-read from the rows.
        management = SessionManagement(store, now=clock)
        reference, record = management.establish(_identity(user))
        assert management.resolve(reference).session_id == record.session_id

        clock.advance(timedelta(minutes=31))
        session.expire_all()
        with pytest.raises(ReauthRequiredError):
            management.resolve(reference)

        session.expire_all()
        revived = management.reauthenticate(record.session_id, _identity(user))
        with pytest.raises(SessionNotFoundError):
            management.resolve(reference)  # the pre-expiry secret is dead
        resolved = management.resolve(revived)
        assert resolved.state is SessionState.ACTIVE
        assert resolved.crm_credential == _identity(user).crm_credential

        management.logout(revived)
        session.expire_all()
        with pytest.raises(SessionEndedError):
            management.resolve(revived)


def test_cipher_binds_the_credential_to_its_session() -> None:
    cipher = CredentialCipher(CREDENTIAL_KEY)
    credential = CrmUserCredential(username="mentor.jane", secret=CRM_SECRET)
    owner, other = uuid7(), uuid7()
    sealed = cipher.seal(credential, session_id=owner)
    assert cipher.open(sealed, session_id=owner) == credential
    with pytest.raises(CredentialSealError):
        cipher.open(sealed, session_id=other)


def test_cipher_refuses_a_non_aes256_key() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        CredentialCipher(b"short")


class TestStoredTokenActionStore:
    def test_round_trip_use_accounting_and_audit_trail(
        self, session: Session, clock: Clock
    ) -> None:
        store = StoredTokenActionStore(session)
        service = TokenActionService(store, signing_key=b"test-signing-key", now=clock)
        token = service.mint(
            token_identity="invitee@cbm.org",
            action_name="invite.accept",
            expires_at=clock() + timedelta(days=3),
            max_uses=1,
        )
        session.expire_all()

        record = service.redeem(token, expected_action="invite.accept")
        assert record.use_count == 1
        session.expire_all()
        with pytest.raises(TokenExhaustedError):
            service.redeem(token, expected_action="invite.accept")

        service.revoke(record.token_action_id)
        session.expire_all()
        loaded = store.load(record.token_action_id)
        assert loaded is not None
        assert loaded.token_identity == "invitee@cbm.org"
        assert loaded.action_name == "invite.accept"
        assert loaded.expires_at == clock() + timedelta(days=3)
        assert loaded.revoked_at == clock()

        events = session.scalars(
            select(TokenAuditEvent).order_by(TokenAuditEvent.token_audit_event_id)
        ).all()
        assert [e.token_event_name for e in events] == ["minted", "redeemed", "revoked"]
        assert all(e.action_token_id == record.token_action_id for e in events)

    def test_missing_token_loads_none(self, session: Session) -> None:
        assert StoredTokenActionStore(session).load(uuid7()) is None

    def test_bound_user_round_trips(
        self, session: Session, user: AppUser, clock: Clock
    ) -> None:
        store = StoredTokenActionStore(session)
        service = TokenActionService(store, signing_key=b"test-signing-key", now=clock)
        service.mint(
            token_identity="mentor@cbm.org",
            action_name="magic.link",
            expires_at=clock() + timedelta(hours=1),
            user_id=user.user_id,
        )
        session.expire_all()
        row = session.scalars(select(ActionToken)).one()
        loaded = store.load(row.action_token_id)
        assert loaded is not None
        assert loaded.user_id == user.user_id


class TestStoredIdentityBridge:
    def _crm_identity(self) -> CrmVerifiedIdentity:
        return CrmVerifiedIdentity(
            crm_user_id="crm-909",
            username="new.sam",
            display_name="Sam New",
            email_address="sam@cbm.org",
            role_names=frozenset({"mentor"}),
            credential=CrmUserCredential(username="new.sam", secret=CRM_SECRET),
        )

    def test_provisions_once_and_finds_thereafter(self, session: Session) -> None:
        bridge = StoredIdentityBridge(session)
        first = bridge.resolve(self._crm_identity())
        again = bridge.resolve(self._crm_identity())
        assert first.user_id == again.user_id
        assert first.role_names == frozenset({"mentor"})
        assert first.crm_credential.secret == CRM_SECRET
        rows = session.scalars(select(AppUser).where(AppUser.crm_user_id == "crm-909")).all()
        assert len(rows) == 1
        assert rows[0].username == "new.sam"
