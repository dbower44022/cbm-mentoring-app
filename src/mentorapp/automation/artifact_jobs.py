"""Export/print artifact jobs and retention trim (REQ-058, REQ-014, DB-S11).

Exports and prints are job types on the one queue producing downloadable
artifacts — big result sets travel as artifacts, never in API responses.
Design:

- :class:`ArtifactStore` is the byte sink the handlers speak. The store owns
  where bytes live and what the download URL is; the queue row records only
  ``artifactUrl`` + ``jobExpiresAt``. The API layer wires the concrete store.
- Both handlers read the entity's generated read view (DB-S9): the view is
  the one canonical read surface — deleted rows already excluded, custom
  attributes promoted, choice labels joined — so an export can never disagree
  with what the grid shows.
- :data:`EXPORT_JOB_TYPE` renders CSV; :data:`PRINT_JOB_TYPE` renders a
  self-contained HTML print document. Each stamps its retention through
  :class:`~mentorapp.automation.worker.JobOutcome`, so ``jobExpiresAt`` is
  set at completion and the trim below can reclaim the artifact.
- :func:`trim_expired_artifacts` is the retention expiry: it discards the
  stored bytes and soft-deletes the expired job rows, appending ``deleted``
  change-feed entries so pollers drop their cached download links.
  :data:`RETENTION_TRIM_JOB_TYPE` runs that trim on the queue itself.

Handlers need the store, but a :data:`~mentorapp.automation.worker.JobHandler`
receives only ``(session, job)`` — so this module exposes handler factories
that close over the store.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from html import escape
from typing import Any, Final, Protocol

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from mentorapp.automation.worker import JobHandler, JobOutcome, PermanentJobError
from mentorapp.observability import get_logger
from mentorapp.storage import (
    BackgroundJob,
    Base,
    ChangeFeedEntry,
    SchemaRegistry,
    read_view_name,
    utcnow,
)

logger = get_logger(__name__)

# Job-type vocabulary for this module (one meaning system-wide, DB-R2).
EXPORT_JOB_TYPE: Final = "export"
PRINT_JOB_TYPE: Final = "print"
RETENTION_TRIM_JOB_TYPE: Final = "artifactRetentionTrim"

# Retention policy per artifact kind: exports are working documents a user
# comes back for; a print document is fetched once, straight to the printer.
EXPORT_RETENTION: Final = timedelta(days=7)
PRINT_RETENTION: Final = timedelta(days=1)


class ArtifactStore(Protocol):
    """Where produced artifacts live; the handlers never touch storage directly."""

    def put(self, name: str, content: bytes, content_type: str) -> str:
        """Store one artifact; returns its download URL."""
        ...

    def discard(self, url: str) -> None:
        """Release the stored bytes behind a URL previously returned by ``put``."""
        ...


def _read_view_rows(
    session: Session, payload: dict[str, Any]
) -> tuple[str, list[str], list[tuple[Any, ...]]]:
    """Resolve the payload against the entity's read view: (entityType, headers, rows).

    Payload contract (wire names): ``entityType`` (required), ``columns`` —
    an optional field-name list projecting the view; omitted means every view
    column. A missing entity or unknown column is a
    :class:`PermanentJobError`: retrying re-reads the same document.
    """
    entity_type = payload.get("entityType")
    # The metadata check both validates the entity and pins the interpolated
    # name to a known table name — no payload text ever reaches the SQL.
    if not isinstance(entity_type, str) or Base.metadata.tables.get(entity_type) is None:
        raise PermanentJobError(f"export/print payload names no known entity: {entity_type!r}")
    has_registry = session.scalars(
        select(SchemaRegistry)
        .where(SchemaRegistry.entity_type == entity_type)
        .where(SchemaRegistry.deleted_at.is_(None))
        .limit(1)
    ).first()
    # A view exists only for registry-described entities (DB-S9); a platform
    # table is a real table with no read surface, and retrying won't grow one.
    if has_registry is None:
        raise PermanentJobError(f"entity has no generated read view: {entity_type!r}")
    result = session.execute(text(f'SELECT * FROM "{read_view_name(entity_type)}"'))
    headers = list(result.keys())
    rows = [tuple(row) for row in result]
    requested = payload.get("columns")
    if requested is not None:
        unknown = [name for name in requested if name not in headers]
        if unknown:
            raise PermanentJobError(f"export/print payload names unknown columns: {unknown}")
        indexes = [headers.index(name) for name in requested]
        headers = list(requested)
        rows = [tuple(row[i] for i in indexes) for row in rows]
    return entity_type, headers, rows


def export_job_handler(store: ArtifactStore) -> JobHandler:
    """The queue handler for :data:`EXPORT_JOB_TYPE`: read view → CSV artifact."""

    def handle(session: Session, job: BackgroundJob) -> JobOutcome:
        entity_type, headers, rows = _read_view_rows(session, job.job_payload)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(headers)
        writer.writerows(rows)
        url = store.put(
            f"export-{job.job_id}.csv", buffer.getvalue().encode("utf-8"), "text/csv"
        )
        logger.info(
            "export artifact produced",
            extra={
                "context": {
                    "jobID": str(job.job_id),
                    "entityType": entity_type,
                    "rowCount": len(rows),
                }
            },
        )
        return JobOutcome(artifact_url=url, artifact_retention=EXPORT_RETENTION)

    return handle


def print_job_handler(store: ArtifactStore) -> JobHandler:
    """The queue handler for :data:`PRINT_JOB_TYPE`: read view → HTML print document."""

    def handle(session: Session, job: BackgroundJob) -> JobOutcome:
        entity_type, headers, rows = _read_view_rows(session, job.job_payload)

        def cell(value: Any) -> str:
            return f"<td>{escape('' if value is None else str(value))}</td>"

        cells = "".join(f"<th>{escape(str(name))}</th>" for name in headers)
        body_rows = "".join(
            "<tr>" + "".join(cell(value) for value in row) + "</tr>" for row in rows
        )
        document = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{escape(entity_type)}</title></head>"
            f"<body><table><thead><tr>{cells}</tr></thead>"
            f"<tbody>{body_rows}</tbody></table></body></html>"
        )
        url = store.put(f"print-{job.job_id}.html", document.encode("utf-8"), "text/html")
        logger.info(
            "print artifact produced",
            extra={
                "context": {
                    "jobID": str(job.job_id),
                    "entityType": entity_type,
                    "rowCount": len(rows),
                }
            },
        )
        return JobOutcome(artifact_url=url, artifact_retention=PRINT_RETENTION)

    return handle


def trim_expired_artifacts(
    session: Session, store: ArtifactStore, *, now: datetime | None = None
) -> int:
    """Reclaim every job past its retention: discard bytes, soft-delete the row.

    Each trimmed row gets a ``deleted`` change-feed entry in the same
    transaction (DB-S10), so consumers holding the download link learn it is
    gone the same way they learned it existed. Returns how many were trimmed.
    """
    now = now or utcnow()
    expired = session.scalars(
        select(BackgroundJob)
        .where(BackgroundJob.deleted_at.is_(None))
        .where(BackgroundJob.job_expires_at <= now)
    ).all()
    for job in expired:
        if job.artifact_url is not None:
            store.discard(job.artifact_url)
            job.artifact_url = None
        job.deleted_at = now
        session.flush()
        session.add(
            ChangeFeedEntry(
                entity_type="backgroundJob",
                record_id=job.job_id,
                record_row_version=job.row_version,
                change_kind="deleted",
            )
        )
    session.flush()
    if expired:
        logger.info(
            "expired artifacts trimmed", extra={"context": {"trimmedCount": len(expired)}}
        )
    return len(expired)


def artifact_retention_trim_job(store: ArtifactStore) -> JobHandler:
    """The queue handler for :data:`RETENTION_TRIM_JOB_TYPE`; no artifact of its own."""

    def handle(session: Session, job: BackgroundJob) -> None:
        trim_expired_artifacts(session, store)
        return None

    return handle
