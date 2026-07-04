"""The background job worker (REQ-058, DB-S11): one queue, one worker contract.

Design of the worker over the ``backgroundJob`` table (WTK-127):

- :func:`enqueue_job` — every producer enqueues the same way: typed job,
  document payload, due immediately unless scheduled. Long-running endpoints
  enqueue and return the job identifier at once.
- :func:`claim_next_job` — the safe lease. One statement selects due pending
  work OR processing rows whose lease expired (a crashed worker's job comes
  back without operator action), locks it with ``FOR UPDATE SKIP LOCKED`` so
  concurrent workers never double-claim (the SQLite test dialect ignores the
  lock hint; tests run serially), and stamps ``lockedUntil``.
- :func:`process_next_job` — dispatch by ``jobType`` through a handler
  registry. Transient failures retry with exponential backoff to a cap;
  :class:`PermanentJobError` (or exhausted attempts) parks the job as
  ``needsAttention`` for a human. ``failed`` is the operator's terminal
  disposition of a parked job via the normal write path — the worker itself
  never gives up silently.
- Artifact production: a handler returns :class:`JobOutcome` with the
  download URL and retention; completion stamps ``artifactUrl`` and
  ``jobExpiresAt`` so the retention trim can reclaim it (DB-S11).
- Every terminal transition appends a ``backgroundJob`` change-feed entry in
  the same transaction (REQ-058: completion surfaces through the feed).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from mentorapp.observability import get_logger
from mentorapp.storage import BackgroundJob, ChangeFeedEntry, utcnow

logger = get_logger(__name__)

DEFAULT_LEASE = timedelta(minutes=5)
DEFAULT_MAX_ATTEMPTS = 5
BACKOFF_BASE = timedelta(seconds=30)
BACKOFF_CAP = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class JobOutcome:
    """What a handler produced: an optional downloadable artifact with retention."""

    artifact_url: str | None = None
    artifact_retention: timedelta | None = None


# A handler receives the worker's session and the claimed job; it returns a
# JobOutcome (or None for jobs with no artifact) and raises to signal failure.
JobHandler = Callable[[Session, BackgroundJob], JobOutcome | None]


class PermanentJobError(Exception):
    """A failure retrying cannot fix — park the job for attention immediately."""


def retry_backoff(attempt_count: int) -> timedelta:
    """Exponential backoff for the Nth failed attempt, capped (DB-S11)."""
    return min(BACKOFF_BASE * (2 ** max(attempt_count - 1, 0)), BACKOFF_CAP)


def enqueue_job(
    session: Session,
    job_type: str,
    payload: dict[str, Any] | None = None,
    *,
    run_after: datetime | None = None,
    acting_user_id: uuid.UUID | None = None,
) -> BackgroundJob:
    """Queue one unit of background work; returns the flushed job (its ID is the receipt)."""
    job = BackgroundJob(
        job_type=job_type,
        job_payload=payload or {},
        created_by=acting_user_id,
        modified_by=acting_user_id,
    )
    if run_after is not None:
        job.run_after = run_after
    session.add(job)
    session.flush()
    return job


def claim_next_job(
    session: Session, *, lease: timedelta = DEFAULT_LEASE, now: datetime | None = None
) -> BackgroundJob | None:
    """Claim the next due job under a lease; ``None`` means the queue is drained.

    Due = pending and ``runAfter`` <= now, or processing with an expired lease
    (crash reclaim). Oldest-due first, job ID as the tiebreak — UUIDv7 keys
    make that insertion order.
    """
    now = now or utcnow()
    job = session.scalars(
        select(BackgroundJob)
        .where(BackgroundJob.deleted_at.is_(None))
        .where(
            or_(
                and_(BackgroundJob.job_status == "pending", BackgroundJob.run_after <= now),
                and_(
                    BackgroundJob.job_status == "processing",
                    BackgroundJob.locked_until < now,
                ),
            )
        )
        .order_by(BackgroundJob.run_after, BackgroundJob.job_id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if job is None:
        return None
    job.job_status = "processing"
    job.locked_until = now + lease
    session.flush()
    return job


def _record_job_event(session: Session, job: BackgroundJob) -> None:
    # Same transaction as the status write (DB-S10): a completed or parked job
    # is never missing its feed event, so pollers see completion via /changes.
    session.add(
        ChangeFeedEntry(
            entity_type="backgroundJob",
            record_id=job.job_id,
            record_row_version=job.row_version,
            change_kind="updated",
        )
    )


def complete_job(
    session: Session,
    job: BackgroundJob,
    outcome: JobOutcome | None = None,
    *,
    now: datetime | None = None,
) -> BackgroundJob:
    """Mark a claimed job completed, stamping artifact URL and retention expiry."""
    now = now or utcnow()
    job.job_status = "completed"
    job.locked_until = None
    if outcome is not None and outcome.artifact_url is not None:
        job.artifact_url = outcome.artifact_url
        if outcome.artifact_retention is not None:
            job.job_expires_at = now + outcome.artifact_retention
    session.flush()
    _record_job_event(session, job)
    session.flush()
    return job


def fail_job(
    session: Session,
    job: BackgroundJob,
    error: str,
    *,
    permanent: bool = False,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    now: datetime | None = None,
) -> BackgroundJob:
    """Record a failed attempt: retry with backoff, or park as ``needsAttention``.

    Transient failures release the lease and push ``runAfter`` out by
    :func:`retry_backoff`; a permanent failure — or the attempt that exhausts
    ``max_attempts`` — parks the job for a human. The error itself goes to the
    structured log with the job context; the row records the state, not prose.
    """
    now = now or utcnow()
    job.attempt_count += 1
    job.locked_until = None
    parked = permanent or job.attempt_count >= max_attempts
    context = {
        "jobID": str(job.job_id),
        "jobType": job.job_type,
        "attemptCount": job.attempt_count,
        "error": error,
    }
    if parked:
        job.job_status = "needsAttention"
        logger.error("background job parked for attention", extra={"context": context})
    else:
        job.job_status = "pending"
        job.run_after = now + retry_backoff(job.attempt_count)
        logger.warning("background job attempt failed; retrying", extra={"context": context})
    session.flush()
    if parked:
        _record_job_event(session, job)
        session.flush()
    return job


def process_next_job(
    session: Session,
    handlers: Mapping[str, JobHandler],
    *,
    lease: timedelta = DEFAULT_LEASE,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    now: datetime | None = None,
) -> bool:
    """Claim and run one job; ``False`` means nothing was due.

    An unregistered ``jobType`` parks immediately — retrying cannot conjure a
    handler, and a silently dropped job is the failure mode DB-S11 forbids.
    """
    job = claim_next_job(session, lease=lease, now=now)
    if job is None:
        return False
    handler = handlers.get(job.job_type)
    if handler is None:
        fail_job(
            session,
            job,
            f"no handler registered for job type {job.job_type!r}",
            permanent=True,
            max_attempts=max_attempts,
            now=now,
        )
        return True
    try:
        outcome = handler(session, job)
    except PermanentJobError as exc:
        fail_job(session, job, str(exc), permanent=True, max_attempts=max_attempts, now=now)
    except Exception as exc:
        logger.exception(
            "background job handler raised",
            extra={"context": {"jobID": str(job.job_id), "jobType": job.job_type}},
        )
        fail_job(session, job, str(exc), max_attempts=max_attempts, now=now)
    else:
        complete_job(session, job, outcome, now=now)
    return True


def run_worker_pass(
    session: Session,
    handlers: Mapping[str, JobHandler],
    *,
    lease: timedelta = DEFAULT_LEASE,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    now: datetime | None = None,
) -> int:
    """Drain everything currently due; returns how many jobs were processed.

    One pass, not a daemon loop: the hosting scheduler owns cadence and
    lifetime, the worker owns a single bounded sweep (a job that fails
    transiently reschedules into the future, so a pass always terminates).
    """
    processed = 0
    while process_next_job(session, handlers, lease=lease, max_attempts=max_attempts, now=now):
        processed += 1
    return processed
