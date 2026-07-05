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
consumes a :class:`VerifiedIdentity` produced by the pluggable verifier (the
ESPO integration today; SSO/MFA later) and never sees credentials.
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

from mentorapp.observability import get_logger
from mentorapp.storage import uuid7

log = get_logger(__name__)


@dataclass(frozen=True)
class VerifiedIdentity:
    """The verifier seam's output: who the user is, proven, with their roles."""

    user_id: uuid.UUID
    role_names: frozenset[str]


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
    """The server-side session; the browser never sees anything but the reference."""

    session_id: uuid.UUID
    user_id: uuid.UUID
    role_names: frozenset[str]
    secret_hash: bytes
    state: SessionState
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    reauth_deadline: datetime | None = None


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
