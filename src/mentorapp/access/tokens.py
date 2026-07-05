"""TokenAction: signed, expiring, use-accounted action links (REQ-007).

A token lets its recipient perform exactly one named action as exactly one
identity, without logging in. The link carries only the token ID and an
HMAC-SHA256 signature over it — unforgeable and unenumerable offline — while
everything that governs the token (identity, action, expiry, use budget,
revocation) lives in the server-side record, where validation and
use-accounting happen. Rotating burden onto the link (JWT-style claims) was
rejected: server-side state is already required for use-accounting and
revocation, so the link stays minimal and nothing security-relevant is
parsed out of client input.

Every mint, redemption, and revocation appends an audit event; the audit
trail is part of the contract, not an optional log line.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.orm import Session

from mentorapp.observability import get_logger
from mentorapp.storage import ActionToken, as_utc, uuid7
from mentorapp.storage import TokenAuditEvent as TokenAuditEventRow

log = get_logger(__name__)


class TokenActionError(Exception):
    """Base of every token refusal; maps to a 403 envelope with the code."""


class TokenInvalidError(TokenActionError):
    """Malformed link, bad signature, unknown token, or wrong action."""


class TokenExpiredError(TokenActionError):
    """The token's expiry has passed."""


class TokenRevokedError(TokenActionError):
    """The token was explicitly revoked."""


class TokenExhaustedError(TokenActionError):
    """The token's use budget is spent."""


@dataclass
class TokenActionRecord:
    """The server-side truth about one action link.

    ``token_identity`` is who the token asserts — email-shaped, a value that
    may predate any ``appUser`` row (WTK-001), so it is never a foreign key.
    ``user_id`` is bound only when the identity already resolves to a user
    (e.g. a password-less magic link for an existing account).
    """

    token_action_id: uuid.UUID
    token_identity: str
    action_name: str
    expires_at: datetime
    max_uses: int
    user_id: uuid.UUID | None = None
    use_count: int = 0
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class TokenAuditEvent:
    """One accountable moment in a token's life: minted, redeemed, or revoked."""

    token_action_id: uuid.UUID
    event_name: str
    occurred_at: datetime


class TokenActionStore(Protocol):
    """The persistence seam for tokens (entity design: WTK-001, storage area)."""

    def load(self, token_action_id: uuid.UUID) -> TokenActionRecord | None: ...

    def save(self, record: TokenActionRecord) -> None: ...

    def append_audit(self, event: TokenAuditEvent) -> None: ...


@dataclass
class InMemoryTokenActionStore:
    """Reference :class:`TokenActionStore` for tests and pre-persistence wiring."""

    records: dict[uuid.UUID, TokenActionRecord] = field(default_factory=dict)
    audit: list[TokenAuditEvent] = field(default_factory=list)

    def load(self, token_action_id: uuid.UUID) -> TokenActionRecord | None:
        return self.records.get(token_action_id)

    def save(self, record: TokenActionRecord) -> None:
        self.records[record.token_action_id] = record

    def append_audit(self, event: TokenAuditEvent) -> None:
        self.audit.append(event)


class TokenActionService:
    """Mint, validate, redeem, and revoke action links."""

    def __init__(
        self,
        store: TokenActionStore,
        *,
        signing_key: bytes,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not signing_key:
            raise ValueError("a non-empty signing key is required")
        self._store = store
        self._signing_key = signing_key
        self._now = now

    def mint(
        self,
        *,
        token_identity: str,
        action_name: str,
        expires_at: datetime,
        max_uses: int = 1,
        user_id: uuid.UUID | None = None,
    ) -> str:
        """Create a token bound to one identity and one action; return the link token.

        ``token_identity`` is the asserted identity (email-shaped; it need
        not name an existing account); pass ``user_id`` only when the
        identity already resolves to an ``appUser`` row.
        """
        if max_uses < 1:
            raise ValueError("max_uses must be at least 1")
        token_action_id = uuid7()
        record = TokenActionRecord(
            token_action_id=token_action_id,
            token_identity=token_identity,
            action_name=action_name,
            expires_at=expires_at,
            max_uses=max_uses,
            user_id=user_id,
        )
        self._store.save(record)
        self._audit(record, "minted")
        return f"{token_action_id.hex}.{self._signature(token_action_id)}"

    def validate(self, token: str, *, expected_action: str) -> TokenActionRecord:
        """Check a token without spending a use; raise the precise typed refusal.

        The signature gates the store lookup, so unsigned guesses cannot
        probe which token IDs exist. ``expected_action`` is the action the
        calling endpoint serves — a token minted for any other action is
        invalid there, which is what binds a token to ONE action.
        """
        record = self._verified_record(token)
        if record.action_name != expected_action:
            raise TokenInvalidError("token was minted for a different action")
        if record.revoked_at is not None:
            raise TokenRevokedError("token revoked")
        if self._now() >= record.expires_at:
            raise TokenExpiredError("token expired")
        if record.use_count >= record.max_uses:
            raise TokenExhaustedError("token use budget spent")
        return record

    def redeem(self, token: str, *, expected_action: str) -> TokenActionRecord:
        """Spend one use and return the record naming who the action runs as."""
        record = self.validate(token, expected_action=expected_action)
        record.use_count += 1
        self._store.save(record)
        self._audit(record, "redeemed")
        return record

    def revoke(self, token_action_id: uuid.UUID) -> None:
        """Kill a token ahead of expiry; idempotent, and always audited."""
        record = self._store.load(token_action_id)
        if record is None:
            raise TokenInvalidError("unknown token")
        if record.revoked_at is None:
            record.revoked_at = self._now()
            self._store.save(record)
        self._audit(record, "revoked")

    def _signature(self, token_action_id: uuid.UUID) -> str:
        digest = hmac.new(self._signing_key, token_action_id.bytes, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def _verified_record(self, token: str) -> TokenActionRecord:
        id_hex, dot, signature = token.partition(".")
        try:
            token_action_id = uuid.UUID(hex=id_hex)
        except ValueError:
            raise TokenInvalidError("malformed token") from None
        if not dot or not hmac.compare_digest(signature, self._signature(token_action_id)):
            raise TokenInvalidError("bad token signature")
        record = self._store.load(token_action_id)
        if record is None:
            raise TokenInvalidError("unknown token")
        return record

    def _audit(self, record: TokenActionRecord, event_name: str) -> None:
        self._store.append_audit(
            TokenAuditEvent(
                token_action_id=record.token_action_id,
                event_name=event_name,
                occurred_at=self._now(),
            )
        )
        # The log context deliberately omits the token identity: identities
        # are email addresses, and PII stays out of the log stream. The
        # tokenActionID is enough to join back to the audit trail.
        log.info(
            "token action " + event_name,
            extra={
                "context": {
                    "tokenActionID": str(record.token_action_id),
                    "actionName": record.action_name,
                    "useCount": record.use_count,
                }
            },
        )


class StoredTokenActionStore:
    """:class:`TokenActionStore` over ``actionToken`` + ``tokenAuditEvent`` (WTK-191).

    Audit rows are append-only by construction — ``append_audit`` only ever
    inserts; the token's current state lives on ``actionToken``, its history
    here. Saves commit for the same reason :class:`StoredSessionStore` gives:
    the process treats a save as durable the moment it returns, and no caller
    above this seam holds the DB session.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def load(self, token_action_id: uuid.UUID) -> TokenActionRecord | None:
        row = self._session.get(ActionToken, token_action_id)
        if row is None:
            return None
        return TokenActionRecord(
            token_action_id=row.action_token_id,
            token_identity=row.token_identity,
            action_name=row.token_action,
            expires_at=as_utc(row.token_expires_at),
            max_uses=row.token_max_uses,
            user_id=row.user_id,
            use_count=row.token_use_count,
            revoked_at=(
                as_utc(row.token_revoked_at) if row.token_revoked_at is not None else None
            ),
        )

    def save(self, record: TokenActionRecord) -> None:
        row = self._session.get(ActionToken, record.token_action_id)
        if row is None:
            row = ActionToken(action_token_id=record.token_action_id)
            self._session.add(row)
        row.token_action = record.action_name
        row.token_identity = record.token_identity
        row.user_id = record.user_id
        row.token_expires_at = record.expires_at
        row.token_max_uses = record.max_uses
        row.token_use_count = record.use_count
        row.token_revoked_at = record.revoked_at
        self._session.commit()

    def append_audit(self, event: TokenAuditEvent) -> None:
        self._session.add(
            TokenAuditEventRow(
                action_token_id=event.token_action_id,
                token_event_name=event.event_name,
                token_event_occurred_at=event.occurred_at,
            )
        )
        self._session.commit()
