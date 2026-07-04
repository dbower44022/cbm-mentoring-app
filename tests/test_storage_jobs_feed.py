"""Tests for the background-job queue and change-feed models (REQ-057, REQ-058, REQ-014)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import or_, select, tuple_
from sqlalchemy.orm import Session

from mentorapp.storage import (
    CHANGE_KINDS,
    JOB_STATUSES,
    BackgroundJob,
    ChangeFeedEntry,
    utcnow,
    uuid7,
)


def test_status_and_change_kind_vocabularies_match_the_standard() -> None:
    assert JOB_STATUSES == ("pending", "processing", "completed", "failed", "needsAttention")
    assert CHANGE_KINDS == ("created", "updated", "deleted", "restored")


def test_background_job_defaults_to_immediately_due_pending(session: Session) -> None:
    job = BackgroundJob(job_type="gridExport", job_payload={"gridKey": "mentorRoster"})
    session.add(job)
    session.commit()

    loaded = session.scalars(select(BackgroundJob)).one()
    assert loaded.job_id.version == 7
    assert loaded.job_status == "pending"
    assert loaded.attempt_count == 0
    assert loaded.run_after is not None
    assert loaded.locked_until is None
    assert loaded.job_expires_at is None
    assert loaded.artifact_url is None
    assert loaded.job_payload == {"gridKey": "mentorRoster"}


def test_claim_predicate_selects_due_work_and_reclaims_expired_leases(
    session: Session,
) -> None:
    now = utcnow()
    due = BackgroundJob(job_type="gridExport", run_after=now - timedelta(minutes=1))
    scheduled = BackgroundJob(job_type="gridExport", run_after=now + timedelta(hours=1))
    leased = BackgroundJob(
        job_type="gridExport",
        job_status="processing",
        locked_until=now + timedelta(minutes=5),
    )
    crashed = BackgroundJob(
        job_type="gridExport",
        job_status="processing",
        locked_until=now - timedelta(minutes=5),
    )
    parked = BackgroundJob(job_type="gridExport", job_status="needsAttention")
    session.add_all([due, scheduled, leased, crashed, parked])
    session.commit()

    # The worker's claim predicate (DB-S11): due pending work, plus processing
    # rows whose lease expired — a crashed worker's job is reclaimed, a live
    # worker's lease is respected.
    claimable = session.scalars(
        select(BackgroundJob).where(
            or_(
                (BackgroundJob.job_status == "pending") & (BackgroundJob.run_after <= now),
                (BackgroundJob.job_status == "processing") & (BackgroundJob.locked_until < now),
            )
        )
    ).all()
    assert {j.job_id for j in claimable} == {due.job_id, crashed.job_id}


def test_retry_and_completion_mutations_bump_row_version(session: Session) -> None:
    job = BackgroundJob(job_type="gridExport")
    session.add(job)
    session.commit()

    job.job_status = "processing"
    job.attempt_count = 1
    job.locked_until = utcnow() + timedelta(minutes=5)
    session.commit()

    job.job_status = "completed"
    job.locked_until = None
    job.artifact_url = "https://artifacts.example/exports/mentor-roster.xlsx"
    job.job_expires_at = utcnow() + timedelta(days=30)
    session.commit()

    assert job.row_version == 3
    assert job.attempt_count == 1


def test_change_feed_catch_up_replays_everything_after_the_watermark(
    session: Session,
) -> None:
    mentor_id = uuid7()
    base_time = utcnow()
    history = [(1, "created"), (2, "updated"), (2, "deleted"), (3, "restored")]
    entries = [
        ChangeFeedEntry(
            entity_type="Mentor",
            record_id=mentor_id,
            record_row_version=version,
            change_kind=kind,
            changed_at=base_time + timedelta(microseconds=offset),
        )
        for offset, (version, kind) in enumerate(history)
    ]
    session.add_all(entries)
    session.commit()

    # The /changes read: one forward keyset scan from the caller's watermark
    # (DB-S10), cursor shape identical to list pagination (DB-S8).
    watermark = (entries[1].changed_at, entries[1].change_feed_entry_id)
    cursor = tuple_(ChangeFeedEntry.changed_at, ChangeFeedEntry.change_feed_entry_id)
    newer = session.scalars(
        select(ChangeFeedEntry).where(cursor > watermark).order_by(*cursor)
    ).all()

    assert [e.change_kind for e in newer] == ["deleted", "restored"]
    assert [e.record_row_version for e in newer] == [2, 3]
    assert all(e.record_id == mentor_id for e in newer)
    # Catch-up from an older watermark replays more, never less (idempotent feed).
    older_watermark = (entries[0].changed_at, entries[0].change_feed_entry_id)
    replayed = session.scalars(select(ChangeFeedEntry).where(cursor > older_watermark)).all()
    assert len(replayed) == 3


def test_queue_and_feed_scan_indexes_exist() -> None:
    job_indexes = {index.name for index in BackgroundJob.__table__.indexes}
    assert {
        "ix_backgroundJob_claim_live",
        "ix_backgroundJob_lease_live",
        "ix_backgroundJob_expiry",
    } <= job_indexes
    feed_indexes = {index.name for index in ChangeFeedEntry.__table__.indexes}
    assert "ix_changeFeedEntry_watermark_live" in feed_indexes
