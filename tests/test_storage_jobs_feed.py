"""Tests for the background-job queue and change-feed models (REQ-057, REQ-058, REQ-014)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mentorapp.storage import (
    CHANGE_KINDS,
    JOB_STATUSES,
    NOTIFICATION_TYPES,
    AppUser,
    BackgroundJob,
    ChangeFeedEntry,
    Notification,
    utcnow,
    uuid7,
)


def test_status_and_change_kind_vocabularies_match_the_standard() -> None:
    assert JOB_STATUSES == ("pending", "processing", "completed", "failed", "needsAttention")
    assert CHANGE_KINDS == ("created", "updated", "deleted", "restored")
    assert NOTIFICATION_TYPES == ("jobCompleted", "jobFailed")


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
    assert loaded.job_progress is None
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
    bell_indexes = {index.name for index in Notification.__table__.indexes}
    assert {
        "ix_notification_bell_live",
        "ix_notification_unread",
        "uq_notification_job_user_live",
        "ix_notification_expiry",
    } <= bell_indexes


def test_job_progress_document_round_trips(session: Session) -> None:
    job = BackgroundJob(job_type="gridExport")
    session.add(job)
    session.commit()

    job.job_progress = {"current": 3, "total": 10}
    session.commit()

    loaded = session.scalars(select(BackgroundJob)).one()
    assert loaded.job_progress == {"current": 3, "total": 10}


def _mentor_user(session: Session, username: str = "mentor") -> AppUser:
    user = AppUser(crm_user_id=f"crm-{username}", username=username)
    session.add(user)
    session.flush()
    return user


def test_notification_defaults_to_an_unread_kept_entry(session: Session) -> None:
    user = _mentor_user(session)
    job = BackgroundJob(job_type="gridExport", job_status="completed")
    session.add(job)
    session.flush()
    session.add(
        Notification(
            user_id=user.user_id,
            notification_type="jobCompleted",
            notification_message="Your mentor roster export is ready to download.",
            job_id=job.job_id,
        )
    )
    session.commit()

    loaded = session.scalars(select(Notification)).one()
    assert loaded.notification_id.version == 7
    assert loaded.read_at is None
    assert loaded.notification_expires_at is None
    assert loaded.job_id == job.job_id


def test_bell_reads_split_unread_from_read(session: Session) -> None:
    user = _mentor_user(session)
    unread = Notification(
        user_id=user.user_id,
        notification_type="jobFailed",
        notification_message="The export could not finish; try a smaller date range.",
    )
    read = Notification(
        user_id=user.user_id,
        notification_type="jobCompleted",
        notification_message="Your export is ready.",
        read_at=utcnow(),
    )
    session.add_all([unread, read])
    session.commit()

    # The badge count (REQ-014): live unread entries for one user.
    unread_ids = set(
        session.scalars(
            select(Notification.notification_id).where(
                Notification.user_id == user.user_id,
                Notification.deleted_at.is_(None),
                Notification.read_at.is_(None),
            )
        )
    )
    assert unread_ids == {unread.notification_id}


def test_bell_list_is_scoped_to_one_user_and_speaks_the_type_vocabulary(
    session: Session,
) -> None:
    mentor = _mentor_user(session, "mentor-a")
    neighbor = _mentor_user(session, "mentor-b")
    session.add_all(
        [
            Notification(
                user_id=mentor.user_id,
                notification_type="jobCompleted",
                notification_message="Your export is ready.",
            ),
            Notification(
                user_id=mentor.user_id,
                notification_type="jobFailed",
                notification_message="The export could not finish; try a smaller date range.",
            ),
            Notification(
                user_id=neighbor.user_id,
                notification_type="jobCompleted",
                notification_message="A different mentor's export.",
            ),
        ]
    )
    session.commit()

    # The bell list read (REQ-014): one user's live entries — a mentor can
    # never see a peer's notifications, and every entry carries its type so
    # the bell can render success and failure entries differently.
    bell = session.scalars(
        select(Notification).where(
            Notification.user_id == mentor.user_id,
            Notification.deleted_at.is_(None),
        )
    ).all()
    assert len(bell) == 2
    assert {n.notification_type for n in bell} == {"jobCompleted", "jobFailed"}
    assert {n.notification_type for n in bell} <= set(NOTIFICATION_TYPES)


def test_read_on_view_stamps_entries_and_clears_the_badge(session: Session) -> None:
    user = _mentor_user(session)
    entries = [
        Notification(
            user_id=user.user_id,
            notification_type="jobCompleted",
            notification_message=f"Export {n} is ready.",
        )
        for n in range(2)
    ]
    session.add_all(entries)
    session.commit()
    versions_before = {e.notification_id: e.row_version for e in entries}

    def badge_count() -> int:
        return (
            session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.user_id == user.user_id,
                    Notification.deleted_at.is_(None),
                    Notification.read_at.is_(None),
                )
            )
            or 0
        )

    assert badge_count() == 2

    # Viewing the bell stamps readAt (REQ-014): the entries stay live in the
    # list — only the badge predicate stops matching them — and the stamp is
    # a versioned update like any other write (DB-S4), never a delete.
    viewed_at = utcnow()
    for entry in entries:
        entry.read_at = viewed_at
    session.commit()

    assert badge_count() == 0
    live = session.scalars(select(Notification).where(Notification.deleted_at.is_(None))).all()
    assert len(live) == 2
    assert all(n.read_at is not None for n in live)
    assert all(n.row_version == versions_before[n.notification_id] + 1 for n in live)


def test_a_reclaimed_rerun_cannot_double_notify(session: Session) -> None:
    # A crash-reclaimed worker re-runs a terminal transition (at-least-once);
    # the partial unique index makes the second bell write collide, not dupe.
    user = _mentor_user(session)
    job = BackgroundJob(job_type="gridExport", job_status="completed")
    session.add(job)
    session.flush()

    def bell_entry() -> Notification:
        return Notification(
            user_id=user.user_id,
            notification_type="jobCompleted",
            notification_message="Your export is ready.",
            job_id=job.job_id,
        )

    session.add(bell_entry())
    session.commit()
    session.add(bell_entry())
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Job-less entries are outside the predicate: several may coexist per user.
    session.add_all(
        [
            Notification(
                user_id=user.user_id,
                notification_type="jobFailed",
                notification_message=f"Entry {n}.",
            )
            for n in range(2)
        ]
    )
    session.commit()


def test_expired_notifications_are_trim_candidates(session: Session) -> None:
    user = _mentor_user(session)
    now = utcnow()
    expired = Notification(
        user_id=user.user_id,
        notification_type="jobCompleted",
        notification_message="Old news.",
        notification_expires_at=now - timedelta(days=1),
    )
    kept = Notification(
        user_id=user.user_id,
        notification_type="jobCompleted",
        notification_message="Recent news.",
        notification_expires_at=now + timedelta(days=30),
    )
    forever = Notification(
        user_id=user.user_id,
        notification_type="jobFailed",
        notification_message="No expiry set.",
    )
    session.add_all([expired, kept, forever])
    session.commit()

    # The retention job's trim predicate — the same scan shape as job expiry.
    due = session.scalars(
        select(Notification).where(
            Notification.deleted_at.is_(None),
            Notification.notification_expires_at <= now,
        )
    ).all()
    assert [n.notification_id for n in due] == [expired.notification_id]
