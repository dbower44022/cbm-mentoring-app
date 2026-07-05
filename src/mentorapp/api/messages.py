"""The stored message center — admin-message persistence behind the home seams (WTK-192).

Implements the WTK-019 :class:`~mentorapp.ui.home_panel.MessageCenter`
behaviors over the ``adminMessage``/``adminMessageReceipt`` tables
(``mentorapp.storage.messages``), plus the admin CRUD the home router's
``get_message_admin`` seam consumes. One class serves both seams so the
user surface and the admin surface can never disagree about what a message
is. It lives in ``api`` (not ``storage``) because it speaks the reference
design's vocabulary — ``AdminMessage`` content objects and its typed
refusals — and raises the API contract's concurrency error; storage modules
import neither.

The REQ-011 invariants, persisted:

- Read is automatic on view: any path that renders a message stamps
  ``messageReadAt`` — there is no separate mark-as-read chore.
- Acknowledgment happens ONLY via the explicit click, is stamped at most
  once (the audit records the first consent), and implies read.
- Expiration filters every render path but never touches receipts: the
  acknowledgment report answers for an expired message exactly as for a
  live one — the books never close.

Each mutating method commits: the center owns its unit of work, exactly as
the preferences router owns its own ``session.commit()``.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.api.errors import StaleRowVersionError
from mentorapp.observability import get_logger
from mentorapp.storage import AdminMessage as AdminMessageRow
from mentorapp.storage import AdminMessageReceipt, as_utc, utcnow
from mentorapp.ui.home_panel import (
    AcknowledgmentNotRequestedError,
    AdminMessage,
    MessagePriority,
    UnknownMessageError,
)

log = get_logger(__name__)


class StoredMessageCenter:
    """Message persistence over one request-scoped session (the WTK-192 backing)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- the MessageCenter reference behaviors (per-user surface) ---------------

    def post(self, message: AdminMessage) -> None:
        """Publish (or re-publish, replacing) an admin message to every user.

        Re-publishing an existing key refreshes the content and soft-deletes
        its receipts — per-user state resets exactly as in the reference
        center, while the superseded acknowledgment stamps remain as
        soft-deleted audit rows rather than vanishing.
        """
        message_id = uuid.UUID(message.key)
        row = self._session.get(AdminMessageRow, message_id)
        if row is None:
            self._session.add(
                AdminMessageRow(
                    admin_message_id=message_id,
                    message_title=message.title,
                    message_body=message.body,
                    message_priority=message.priority.value,
                    requires_acknowledgment_flag=message.requires_acknowledgment,
                    message_expires_at=message.expires_at,
                    created_at=message.posted_at,
                    created_by=uuid.UUID(message.posted_by),
                )
            )
        else:
            now = utcnow()
            row.message_title = message.title
            row.message_body = message.body
            row.message_priority = message.priority.value
            row.requires_acknowledgment_flag = message.requires_acknowledgment
            row.message_expires_at = message.expires_at
            row.deleted_at = None
            row.deleted_by = None
            for receipt in self._receipts(message_id):
                receipt.deleted_at = now
        self._session.commit()
        log.info(
            "admin message posted",
            extra={"context": {"messageKey": message.key, "priority": message.priority}},
        )

    def visible_messages(self, now: datetime) -> tuple[AdminMessage, ...]:
        """Every live, unexpired message, newest first (the dashlet's order)."""
        return tuple(self._content(row) for row in self._live_rows() if self._visible(row, now))

    def unread_count(self, user_id: str, now: datetime) -> int:
        """The Home badge: unexpired messages this user has not yet seen."""
        read = self._read_message_ids(uuid.UUID(user_id))
        return sum(
            1
            for row in self._live_rows()
            if self._visible(row, now) and row.admin_message_id not in read
        )

    def view_home(self, user_id: str, now: datetime) -> tuple[AdminMessage, ...]:
        """Render the message dashlet: returns AND reads its messages (auto-read)."""
        viewer = uuid.UUID(user_id)
        rows = [row for row in self._live_rows() if self._visible(row, now)]
        for row in rows:
            self._stamp_read(row.admin_message_id, viewer, now)
        self._session.commit()
        return tuple(self._content(row) for row in rows)

    def view_message(self, user_id: str, message_key: str, now: datetime) -> AdminMessage:
        """Render ONE message (the banner's open act); expired refuses like unknown."""
        row = self._live_row(message_key)
        if not self._visible(row, now):
            raise UnknownMessageError(message_key)
        self._stamp_read(row.admin_message_id, uuid.UUID(user_id), now)
        self._session.commit()
        return self._content(row)

    def has_acknowledged(self, user_id: str, message_key: str) -> bool:
        """Quiet per-user acknowledgment state, for rendering the message."""
        row = self._live_row(message_key)
        receipt = self._receipt(row.admin_message_id, uuid.UUID(user_id))
        return receipt is not None and receipt.message_acknowledged_at is not None

    def urgent_banner(self, user_id: str, now: datetime) -> tuple[AdminMessage, ...]:
        """Unexpired urgent messages this user has NOT read — reading clears them."""
        read = self._read_message_ids(uuid.UUID(user_id))
        return tuple(
            self._content(row)
            for row in self._live_rows()
            if self._visible(row, now)
            and row.message_priority == MessagePriority.URGENT.value
            and row.admin_message_id not in read
        )

    def acknowledge(self, user_id: str, message_key: str) -> None:
        """Record one user's EXPLICIT acknowledgment click; works on expired keys.

        The stamp is written at most once — a repeat click never rewrites the
        first consent's timestamp. Acknowledging implies read.
        """
        row = self._live_row(message_key)
        if not row.requires_acknowledgment_flag:
            raise AcknowledgmentNotRequestedError(message_key)
        now = utcnow()
        receipt = self._stamp_read(row.admin_message_id, uuid.UUID(user_id), now)
        if receipt.message_acknowledged_at is None:
            receipt.message_acknowledged_at = now
        self._session.commit()
        log.info(
            "admin message acknowledged",
            extra={"context": {"messageKey": message_key, "userId": user_id}},
        )

    def outstanding_acknowledgments(
        self, message_key: str, user_ids: Collection[str]
    ) -> tuple[str, ...]:
        """The admin audit: who, of ``user_ids``, has not acknowledged yet.

        Expiration does not close the books — receipts outlive the message's
        presence on Home, so this answers identically after expiry.
        """
        row = self._live_row(message_key)
        if not row.requires_acknowledgment_flag:
            raise AcknowledgmentNotRequestedError(message_key)
        acknowledged = {
            str(receipt.user_id)
            for receipt in self._receipts(row.admin_message_id)
            if receipt.message_acknowledged_at is not None
        }
        return tuple(u for u in user_ids if u not in acknowledged)

    # --- admin CRUD (the get_message_admin seam) --------------------------------

    def list_messages(self) -> list[dict[str, Any]]:
        """Every live message, newest first, INCLUDING expired ones.

        The admin surface manages the corpus and reads reports after expiry,
        so unlike every user path it must still see expired messages.
        """
        return [self._record(row) for row in self._live_rows()]

    def update_message(
        self, message_key: str, changes: Mapping[str, Any], row_version: int
    ) -> dict[str, Any]:
        """Apply a per-field PATCH under optimistic concurrency (DB-S4/S12).

        ``changes`` holds only the fields the caller sent (wire names). A
        stale ``row_version`` raises :class:`StaleRowVersionError` carrying
        the current record — the 409-with-current-record contract.
        """
        row = self._live_row(message_key)
        if row.row_version != row_version:
            raise StaleRowVersionError(self._record(row))
        if "title" in changes:
            row.message_title = changes["title"]
        if "body" in changes:
            row.message_body = changes["body"]
        if "priority" in changes:
            row.message_priority = MessagePriority(changes["priority"]).value
        if "requiresAcknowledgment" in changes:
            row.requires_acknowledgment_flag = bool(changes["requiresAcknowledgment"])
        if "expiresAt" in changes:
            row.message_expires_at = changes["expiresAt"]
        self._session.commit()
        return self._record(row)

    def delete_message(self, message_key: str, deleted_by: uuid.UUID) -> None:
        """Soft-delete one message (never physical, DB-S3); receipts remain as audit."""
        row = self._live_row(message_key)
        row.deleted_at = utcnow()
        row.deleted_by = deleted_by
        self._session.commit()
        log.info(
            "admin message deleted",
            extra={"context": {"messageKey": message_key, "deletedBy": str(deleted_by)}},
        )

    # --- internals ---------------------------------------------------------------

    def _live_rows(self) -> list[AdminMessageRow]:
        return list(
            self._session.scalars(
                select(AdminMessageRow)
                .where(AdminMessageRow.deleted_at.is_(None))
                .order_by(
                    AdminMessageRow.created_at.desc(),
                    AdminMessageRow.admin_message_id.desc(),
                )
            )
        )

    def _live_row(self, message_key: str) -> AdminMessageRow:
        # A malformed key is as unknown as an absent one — same caller answer.
        try:
            message_id = uuid.UUID(message_key)
        except ValueError:
            raise UnknownMessageError(message_key) from None
        row = self._session.get(AdminMessageRow, message_id)
        if row is None or row.deleted_at is not None:
            raise UnknownMessageError(message_key)
        return row

    @staticmethod
    def _visible(row: AdminMessageRow, now: datetime) -> bool:
        # Expiry filtering happens here, in Python, not SQL: SQLite stores
        # timestamps as strings, and the corpus is small (REQ-011 messages,
        # not records) — correctness across both dialects beats a WHERE.
        return row.message_expires_at is None or now < as_utc(row.message_expires_at)

    @staticmethod
    def _content(row: AdminMessageRow) -> AdminMessage:
        return AdminMessage(
            key=str(row.admin_message_id),
            title=row.message_title,
            body=row.message_body,
            posted_by=str(row.created_by),
            posted_at=as_utc(row.created_at),
            expires_at=(
                as_utc(row.message_expires_at) if row.message_expires_at is not None else None
            ),
            priority=MessagePriority(row.message_priority),
            requires_acknowledgment=row.requires_acknowledgment_flag,
        )

    def _record(self, row: AdminMessageRow) -> dict[str, Any]:
        # The admin wire shape: the user payload's fields plus what admin
        # edits need (rowVersion for the PATCH round-trip).
        content = self._content(row)
        return {
            "messageKey": content.key,
            "title": content.title,
            "body": content.body,
            "postedBy": content.posted_by,
            "postedAt": content.posted_at,
            "expiresAt": content.expires_at,
            "priority": content.priority.value,
            "requiresAcknowledgment": content.requires_acknowledgment,
            "rowVersion": row.row_version,
        }

    def _receipts(self, message_id: uuid.UUID) -> list[AdminMessageReceipt]:
        return list(
            self._session.scalars(
                select(AdminMessageReceipt)
                .where(AdminMessageReceipt.deleted_at.is_(None))
                .where(AdminMessageReceipt.admin_message_id == message_id)
            )
        )

    def _receipt(self, message_id: uuid.UUID, user_id: uuid.UUID) -> AdminMessageReceipt | None:
        return self._session.scalars(
            select(AdminMessageReceipt)
            .where(AdminMessageReceipt.deleted_at.is_(None))
            .where(AdminMessageReceipt.admin_message_id == message_id)
            .where(AdminMessageReceipt.user_id == user_id)
        ).first()

    def _read_message_ids(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        return set(
            self._session.scalars(
                select(AdminMessageReceipt.admin_message_id)
                .where(AdminMessageReceipt.deleted_at.is_(None))
                .where(AdminMessageReceipt.user_id == user_id)
                .where(AdminMessageReceipt.message_read_at.is_not(None))
            )
        )

    def _stamp_read(
        self, message_id: uuid.UUID, user_id: uuid.UUID, now: datetime
    ) -> AdminMessageReceipt:
        # Get-or-create keeps one live receipt per (message, user) — the
        # partial unique index backstops a concurrent double-create.
        receipt = self._receipt(message_id, user_id)
        if receipt is None:
            receipt = AdminMessageReceipt(admin_message_id=message_id, user_id=user_id)
            self._session.add(receipt)
        if receipt.message_read_at is None:
            receipt.message_read_at = now
        return receipt
