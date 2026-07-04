"""Change-feed sync (REQ-057, DB-S10): idempotent catch-up and push transport.

The feed contract, designed once for every consumer:

- The watermark is the keyset pair ``(changedAt, changeFeedEntryID)`` — the
  same sort-value-plus-ID-tiebreak shape as list pagination (DB-S8), so ties
  on ``changedAt`` never skip or duplicate an entry within one read.
- :func:`read_changes_since` is the catch-up read behind ``GET /changes``:
  one forward scan from the caller's watermark on the feed's partial index.
  Delivery is at-least-once — replaying from any older watermark re-reads
  entries — so every consumer MUST be idempotent, deduplicating on
  ``changeFeedEntryID``. That contract is what makes crash recovery "resume
  from your last watermark" and nothing more.
- :func:`sync_change_feed` is the push side: batches are pushed through a
  :class:`FeedPushTransport` and the watermark advances only after a batch is
  accepted, so a transport failure re-pushes that batch on the next sync
  (at-least-once again, same dedup contract) and never loses entries.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import NamedTuple, Protocol

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from mentorapp.observability import get_logger
from mentorapp.storage import ChangeFeedEntry

logger = get_logger(__name__)

DEFAULT_BATCH_SIZE = 500


class FeedWatermark(NamedTuple):
    """A consumer's resume point: the keyset of the last entry it has seen."""

    changed_at: datetime
    entry_id: uuid.UUID


def watermark_of(entry: ChangeFeedEntry) -> FeedWatermark:
    """The watermark that resumes immediately after ``entry``."""
    return FeedWatermark(entry.changed_at, entry.change_feed_entry_id)


class FeedSyncError(Exception):
    """A transport rejected a batch. ``watermark`` is the last DURABLE point.

    Everything at or before ``watermark`` was accepted; the caller persists it
    and the next sync re-pushes the rejected batch (at-least-once). The
    transport's own exception rides along as ``__cause__``.
    """

    def __init__(self, watermark: FeedWatermark | None) -> None:
        super().__init__("change-feed push rejected by transport")
        self.watermark = watermark


class FeedPushTransport(Protocol):
    """Where pushed feed batches go (webhook, queue, socket fan-out).

    ``push`` raises to reject a batch; delivery is at-least-once, so the
    receiving side deduplicates on ``changeFeedEntryID``.
    """

    def push(self, entries: Sequence[ChangeFeedEntry]) -> None: ...


def read_changes_since(
    session: Session,
    since: FeedWatermark | None,
    *,
    limit: int = DEFAULT_BATCH_SIZE,
) -> tuple[list[ChangeFeedEntry], FeedWatermark | None]:
    """One catch-up batch after ``since`` (``None`` = from the beginning).

    Returns the entries in watermark order plus the watermark to resume from;
    a ``None`` watermark back means the batch was empty and the caller is
    caught up. The keyset predicate is expanded (not a row-value tuple) so the
    SQLite test dialect runs the same statement shape as Postgres (DB-S8).
    """
    stmt = (
        select(ChangeFeedEntry)
        .where(ChangeFeedEntry.deleted_at.is_(None))
        .order_by(ChangeFeedEntry.changed_at, ChangeFeedEntry.change_feed_entry_id)
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(
            or_(
                ChangeFeedEntry.changed_at > since.changed_at,
                and_(
                    ChangeFeedEntry.changed_at == since.changed_at,
                    ChangeFeedEntry.change_feed_entry_id > since.entry_id,
                ),
            )
        )
    entries = list(session.scalars(stmt))
    if not entries:
        return [], None
    return entries, watermark_of(entries[-1])


def sync_change_feed(
    session: Session,
    transport: FeedPushTransport,
    since: FeedWatermark | None,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> FeedWatermark | None:
    """Push everything after ``since`` through ``transport``; return the new watermark.

    Batches advance the watermark one accepted push at a time. A transport
    failure raises :class:`FeedSyncError` carrying the last durable watermark
    (never swallowed, never lost): the caller persists that mark and the next
    sync resumes by re-pushing the rejected batch. Returns ``since`` unchanged
    when there was nothing to push.
    """
    watermark = since
    while True:
        entries, next_watermark = read_changes_since(session, watermark, limit=batch_size)
        if next_watermark is None:
            return watermark
        try:
            transport.push(entries)
        except Exception as exc:
            logger.exception(
                "change-feed push rejected; will re-push from watermark",
                extra={
                    "context": {
                        "batchSize": len(entries),
                        "watermark": str(watermark.entry_id) if watermark else None,
                    }
                },
            )
            raise FeedSyncError(watermark) from exc
        watermark = next_watermark
