"""SessionManagement: server-side sessions with in-place re-auth (REQ-005).

The browser holds only an opaque reference (``<sessionID hex>.<secret>``);
expiry, idle timeout, and revocation all live server-side on the session
record, so revoking the record ends access everywhere at once. All of a
user's windows share the one session reference, which is what makes the
multi-window guarantees mechanical rather than coordinated:

- **Dirty-window guard:** an expired session is not destroyed — it moves to
  ``REAUTH_PENDING`` and is kept for a grace period. Every window gets a
  re-auth challenge in place instead of a dead session, so unsaved work
  survives on the client while the server waits.
- **One re-login restores all windows:** re-authentication revives the SAME
  session identity (rotating only the secret), so the one new reference
  works in every window.
- **Cross-window logout:** logout ends the shared record; every window's
  next request fails closed.

Credential verification is deliberately not here (REQ-008): establishment
consumes a :class:`VerifiedIdentity` produced by the pluggable verifier and
resolved through the identity bridge (``access/identity.py``) — this module
never sees credentials, only the CRM token the bridge carried through.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy.orm import Session

from mentorapp.access.credentials import CredentialCipher
from mentorapp.access.identity import VerifiedIdentity
from mentorapp.crm.auth import CrmUserCredential
from mentorapp.observability import get_logger
from mentorapp.storage import AuthSession, as_utc, uuid7

log = get_logger(__name__)


class SessionState(enum.StrEnum):
    ACTIVE = "active"
    REAUTH_PENDING = "reauthPending"
    ENDED = "ended"


class SessionNotFoundError(Exception):
    """Unknown or malformed session reference; maps to a 401 envelope."""


class SessionEndedError(Exception):
    """The session was logged out or its re-auth window lapsed; 401, fresh login."""


class ReauthRequiredError(Exception):
    """The session expired but is revivable: re-authenticate in place.

    Carries the ``session_id`` so the API can challenge every window against
    the same session; the client keeps its unsaved work while it asks.
    """

    def __init__(self, session_id: uuid.UUID) -> None:
        self.session_id = session_id
        super().__init__("session expired; re-authentication required")


class IdentityMismatchError(Exception):
    """Re-auth presented a different user than the session's owner; refused."""


@dataclass
class SessionRecord:
    """The server-side session; the browser never sees anything but the reference.

    ``crm_credential`` is the session-scoped custody of the CRM-issued
    act-as-user token for :class:`~mentorapp.crm.auth.CrmAccess` — WTK-003
    assigns custody here, not to any per-user store. At rest it lives
    AEAD-encrypted on ``authSession`` (``crmCredentialEncrypted``), never
    plaintext; each login/reauth recaptures it fresh.
    """

    session_id: uuid.UUID
    user_id: uuid.UUID
    role_names: frozenset[str]
    secret_hash: bytes
    state: SessionState
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    reauth_deadline: datetime | None = None
    crm_credential: CrmUserCredential | None = None


class SessionStore(Protocol):
    """The persistence seam for sessions (entity design: WTK-001, storage area)."""

    def load(self, session_id: uuid.UUID) -> SessionRecord | None: ...

    def save(self, record: SessionRecord) -> None: ...


@dataclass
class InMemorySessionStore:
    """Reference :class:`SessionStore` for tests and pre-persistence wiring."""

    records: dict[uuid.UUID, SessionRecord] = field(default_factory=dict)

    def load(self, session_id: uuid.UUID) -> SessionRecord | None:
        return self.records.get(session_id)

    def save(self, record: SessionRecord) -> None:
        self.records[record.session_id] = record


def _hash_secret(secret: str) -> bytes:
    # The store never holds the raw secret: a leaked session table must not
    # yield usable references. SHA-256 (not a slow KDF) is deliberate — the
    # secret is 256 random bits, not a guessable password.
    return hashlib.sha256(secret.encode("ascii")).digest()


class SessionManagement:
    """The session lifecycle: establish, resolve, re-authenticate, logout."""

    def __init__(
        self,
        store: SessionStore,
        *,
        idle_timeout: timedelta = timedelta(minutes=30),
        absolute_lifetime: timedelta = timedelta(hours=12),
        reauth_grace: timedelta = timedelta(hours=12),
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._idle_timeout = idle_timeout
        self._absolute_lifetime = absolute_lifetime
        self._reauth_grace = reauth_grace
        self._now = now

    def establish(self, identity: VerifiedIdentity) -> tuple[str, SessionRecord]:
        """Open a session for a verified identity; return the opaque reference."""
        now = self._now()
        session_id = uuid7()
        reference, secret_hash = self._mint_reference(session_id)
        record = SessionRecord(
            session_id=session_id,
            user_id=identity.user_id,
            role_names=identity.role_names,
            secret_hash=secret_hash,
            state=SessionState.ACTIVE,
            created_at=now,
            last_seen_at=now,
            expires_at=now + self._absolute_lifetime,
            crm_credential=identity.crm_credential,
        )
        self._store.save(record)
        log.info(
            "session established",
            extra={"context": {"sessionID": str(session_id), "userID": str(identity.user_id)}},
        )
        return reference, record

    def resolve(self, reference: str) -> SessionRecord:
        """Authenticate one request; the ONE gate every session-bound call passes.

        Expiry is enforced here, at the server, on every resolve — an idle or
        aged-out session transitions to ``REAUTH_PENDING`` (the dirty-window
        guard) rather than being destroyed, and the raised
        :class:`ReauthRequiredError` tells every window to re-auth in place.
        """
        record, secret = self._lookup(reference)
        if not hmac.compare_digest(record.secret_hash, _hash_secret(secret)):
            raise SessionNotFoundError("unknown session reference")
        now = self._now()
        if record.state is SessionState.ENDED:
            raise SessionEndedError("session ended")
        if record.state is SessionState.REAUTH_PENDING:
            self._expire_pending_past_grace(record, now)
            raise ReauthRequiredError(record.session_id)
        if now >= record.expires_at or now - record.last_seen_at >= self._idle_timeout:
            record.state = SessionState.REAUTH_PENDING
            record.reauth_deadline = now + self._reauth_grace
            self._store.save(record)
            log.info(
                "session expired to reauth-pending",
                extra={"context": {"sessionID": str(record.session_id)}},
            )
            raise ReauthRequiredError(record.session_id)
        record.last_seen_at = now
        self._store.save(record)
        return record

    def reauthenticate(self, session_id: uuid.UUID, identity: VerifiedIdentity) -> str:
        """Revive an expired session in place; one re-login restores all windows.

        Only the session's own user may revive it — a different verified
        identity is refused and the session stays pending, because another
        window may still re-auth as the right user. The secret rotates on
        revival, so references issued before expiry are dead afterward.
        """
        record = self._store.load(session_id)
        if record is None:
            raise SessionNotFoundError("unknown session")
        now = self._now()
        if record.state is SessionState.ENDED:
            raise SessionEndedError("session ended")
        self._expire_pending_past_grace(record, now)
        if identity.user_id != record.user_id:
            log.info(
                "session reauth refused: identity mismatch",
                extra={"context": {"sessionID": str(session_id)}},
            )
            raise IdentityMismatchError("re-authentication must present the session's user")
        reference, secret_hash = self._mint_reference(session_id)
        record.secret_hash = secret_hash
        record.role_names = identity.role_names
        # The fresh verification exchange issued a fresh CRM token; the stale
        # one (expired or dropped by the CRM) is replaced, never kept around.
        record.crm_credential = identity.crm_credential
        record.state = SessionState.ACTIVE
        record.reauth_deadline = None
        record.last_seen_at = now
        record.expires_at = now + self._absolute_lifetime
        self._store.save(record)
        log.info(
            "session reauthenticated in place",
            extra={"context": {"sessionID": str(session_id), "userID": str(record.user_id)}},
        )
        return reference

    def require_reauth(self, session_id: uuid.UUID) -> None:
        """Force an ACTIVE session into the in-place re-auth flow.

        The :class:`~mentorapp.crm.auth.CrmCredentialExpiredError` path: when
        the CRM stops honouring the stored credential mid-session, the caller
        flips the session to the SAME revivable ``REAUTH_PENDING`` state as
        natural expiry — every window gets the in-place challenge, one
        re-login restores them all and recaptures a fresh credential. A
        session already pending keeps its deadline (idempotent); an ended
        session stays ended.
        """
        record = self._store.load(session_id)
        if record is None:
            raise SessionNotFoundError("unknown session")
        if record.state is SessionState.ENDED:
            raise SessionEndedError("session ended")
        if record.state is SessionState.ACTIVE:
            record.state = SessionState.REAUTH_PENDING
            record.reauth_deadline = self._now() + self._reauth_grace
            self._store.save(record)
            log.info(
                "session forced to reauth-pending",
                extra={"context": {"sessionID": str(record.session_id)}},
            )

    def logout(self, reference: str) -> None:
        """Explicit logout: end the shared record, so every window fails closed.

        Ending is idempotent and works from any live state — a user logging
        out of a reauth-pending window means it, unsaved work notwithstanding.
        """
        record, secret = self._lookup(reference)
        if not hmac.compare_digest(record.secret_hash, _hash_secret(secret)):
            raise SessionNotFoundError("unknown session reference")
        record.state = SessionState.ENDED
        record.reauth_deadline = None
        self._store.save(record)
        log.info(
            "session logged out",
            extra={"context": {"sessionID": str(record.session_id)}},
        )

    def _mint_reference(self, session_id: uuid.UUID) -> tuple[str, bytes]:
        secret = secrets.token_urlsafe(32)
        return f"{session_id.hex}.{secret}", _hash_secret(secret)

    def _lookup(self, reference: str) -> tuple[SessionRecord, str]:
        session_hex, dot, secret = reference.partition(".")
        try:
            session_id = uuid.UUID(hex=session_hex)
        except ValueError:
            raise SessionNotFoundError("unknown session reference") from None
        record = self._store.load(session_id)
        if not dot or not secret or record is None:
            raise SessionNotFoundError("unknown session reference")
        return record, secret

    def _expire_pending_past_grace(self, record: SessionRecord, now: datetime) -> None:
        if (
            record.state is SessionState.REAUTH_PENDING
            and record.reauth_deadline is not None
            and now >= record.reauth_deadline
        ):
            record.state = SessionState.ENDED
            record.reauth_deadline = None
            self._store.save(record)
            raise SessionEndedError("re-authentication window lapsed")


class StoredSessionStore:
    """:class:`SessionStore` over the persisted ``authSession`` rows (WTK-191).

    The CRM credential is sealed/opened through :class:`CredentialCipher`
    (FND-006): plaintext never reaches the column, and an ENDED session's
    credential is dropped rather than stored — custody ends with the session.
    Each ``save`` commits, because the process layer treats a save as durable
    even when it raises immediately afterwards (a dirty-window transition or a
    grace-lapse ending must persist behind the very 401 it causes), and no
    endpoint above this seam ever sees the DB session to commit it.
    """

    def __init__(self, session: Session, *, cipher: CredentialCipher) -> None:
        self._session = session
        self._cipher = cipher

    def load(self, session_id: uuid.UUID) -> SessionRecord | None:
        row = self._session.get(AuthSession, session_id)
        if row is None:
            return None
        credential = (
            self._cipher.open(row.crm_credential_encrypted, session_id=row.auth_session_id)
            if row.crm_credential_encrypted is not None
            else None
        )
        return SessionRecord(
            session_id=row.auth_session_id,
            user_id=row.user_id,
            role_names=frozenset(row.session_role_names or ()),
            secret_hash=bytes.fromhex(row.session_secret_hash),
            state=SessionState(row.session_state),
            created_at=as_utc(row.created_at),
            last_seen_at=as_utc(row.session_last_seen_at),
            expires_at=as_utc(row.session_expires_at),
            reauth_deadline=(
                as_utc(row.session_reauth_deadline)
                if row.session_reauth_deadline is not None
                else None
            ),
            crm_credential=credential,
        )

    def save(self, record: SessionRecord) -> None:
        row = self._session.get(AuthSession, record.session_id)
        if row is None:
            # The session's own user is the acting identity for its audit
            # columns; createdAt is the process clock's establishment time,
            # not the insert time.
            row = AuthSession(
                auth_session_id=record.session_id,
                user_id=record.user_id,
                created_at=record.created_at,
                created_by=record.user_id,
            )
            self._session.add(row)
        row.session_secret_hash = record.secret_hash.hex()
        row.session_state = record.state.value
        row.session_expires_at = record.expires_at
        row.session_reauth_deadline = record.reauth_deadline
        row.session_last_seen_at = record.last_seen_at
        row.session_role_names = sorted(record.role_names)
        row.crm_credential_encrypted = (
            self._cipher.seal(record.crm_credential, session_id=record.session_id)
            if record.crm_credential is not None and record.state is not SessionState.ENDED
            else None
        )
        row.modified_by = record.user_id
        self._session.commit()
