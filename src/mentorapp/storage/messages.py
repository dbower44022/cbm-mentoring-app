"""Admin-message persistence: the message table and per-user receipts (WTK-192, REQ-011).

The storage backing for the WTK-019 :class:`~mentorapp.ui.home_panel.MessageCenter`
reference design. Two tables, deliberately NOT folded into the WTK-023
``notification`` table: a bell entry is one user's pointer at one job's
terminal transition, while an admin message is broadcast content every user
carries independent read/acknowledgment state for — merging them would force
per-user row fan-out on every post.

- ``adminMessage`` — the posted content. ``createdAt``/``createdBy`` ARE the
  posted-at/posted-by facts (DB-R2: a second postedAt column would give one
  meaning two names). Expiration is admin-set and optional
  (``messageExpiresAt``); the priority vocabulary is
  ``mentorapp.ui.home_panel.MessagePriority`` — app-validated, never a
  database enum (DB-S7). Urgent-banner state is derived, never stored:
  priority urgent AND no read receipt for the viewing user.
- ``adminMessageReceipt`` — one row per (message, user) holding
  ``messageReadAt`` (stamped by view — auto-read) and
  ``messageAcknowledgedAt`` (stamped ONLY by the explicit acknowledge click).
  Expiration filters the message read paths and never touches receipts, so
  the acknowledgment audit SURVIVES message expiration — the admin report
  reads receipts after the message has left Home.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from mentorapp.storage.base import Base, StructuralColumnsMixin, uuid7

_LIVE = text('"deletedAt" IS NULL')


class AdminMessage(StructuralColumnsMixin, Base):
    """One admin-posted message, broadcast to every user (REQ-011)."""

    __tablename__ = "adminMessage"
    __table_args__ = (
        # The dashlet read: live messages newest first (expiry is a cheap
        # residual filter — messages are few; a partial-by-expiry index would
        # have to be rebuilt against "now" to help).
        Index(
            "ix_adminMessage_dashlet_live",
            "createdAt",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    admin_message_id: Mapped[uuid.UUID] = mapped_column(
        "adminMessageID", primary_key=True, default=uuid7
    )
    message_title: Mapped[str] = mapped_column("messageTitle", String(200), nullable=False)
    message_body: Mapped[str] = mapped_column("messageBody", String(2000), nullable=False)
    message_priority: Mapped[str] = mapped_column(
        "messagePriority", String(50), nullable=False, default="normal"
    )
    requires_acknowledgment_flag: Mapped[bool] = mapped_column(
        "requiresAcknowledgmentFlag", nullable=False, default=False
    )
    # Null = never expires. An expired message leaves every user surface but
    # its row (and every receipt) remains — the books never close (REQ-011).
    message_expires_at: Mapped[datetime | None] = mapped_column(
        "messageExpiresAt", DateTime(timezone=True), default=None
    )


class AdminMessageReceipt(StructuralColumnsMixin, Base):
    """One user's read/acknowledgment state for one message (REQ-011).

    Created lazily on first read or acknowledgment — a user with no receipt
    has simply not seen the message yet (the badge counts by absence).
    ``messageAcknowledgedAt`` is set only by the explicit acknowledge click,
    never by any view path, and is written at most once: the audit records
    the FIRST consent, and a repeat click must not rewrite history.
    """

    __tablename__ = "adminMessageReceipt"
    __table_args__ = (
        # At most one live receipt per (message, user): read and acknowledge
        # race-merge into one row instead of double-counting the audit. Also
        # serves the admin report's by-message scan (leading column).
        Index(
            "uq_adminMessageReceipt_message_user_live",
            "adminMessageID",
            "userID",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
        # The badge/banner anti-join: one user's receipts across messages.
        Index(
            "ix_adminMessageReceipt_user_live",
            "userID",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    admin_message_receipt_id: Mapped[uuid.UUID] = mapped_column(
        "adminMessageReceiptID", primary_key=True, default=uuid7
    )
    admin_message_id: Mapped[uuid.UUID] = mapped_column(
        "adminMessageID", ForeignKey("adminMessage.adminMessageID"), nullable=False
    )
    # A real FK (the notification precedent, not userPreference's soft userID):
    # receipts are only ever stamped for authenticated session users, so an
    # orphan receipt is a defect the database should refuse.
    user_id: Mapped[uuid.UUID] = mapped_column(
        "userID", ForeignKey("appUser.userID"), nullable=False
    )
    # Null = not yet read. Stamped by any view that rendered the message
    # (auto-read on view); reading is what clears the urgent banner.
    message_read_at: Mapped[datetime | None] = mapped_column(
        "messageReadAt", DateTime(timezone=True), default=None
    )
    # Null = not acknowledged. Only the explicit click sets this (REQ-011);
    # it implies read, so the writer stamps messageReadAt alongside.
    message_acknowledged_at: Mapped[datetime | None] = mapped_column(
        "messageAcknowledgedAt", DateTime(timezone=True), default=None
    )
