"""The postal-reference refresh job (REQ-061): reference data on the one queue.

``postalCode`` is refreshed as a job type on the single background queue —
never hand-edited (DB-S13). The refresh is an idempotent snapshot upsert
keyed on ``(countryCode, normalized postalCodeValue)``: rerunning the same
snapshot is a no-op (no row versions bump, no phantom history), changed
city/state pairs update in place, and a full snapshot soft-deletes live rows
it no longer contains so stale codes stop feeding auto-fill while their
history survives (DB-S3).

:data:`POSTAL_REFRESH_JOB_TYPE` + :func:`postal_reference_refresh_job` are
the worker registration; :func:`refresh_postal_reference` is the engine the
handler (and any seed migration) composes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.automation.normalization import normalize_postal_code
from mentorapp.automation.worker import JobOutcome, PermanentJobError
from mentorapp.observability import get_logger
from mentorapp.storage import BackgroundJob, PostalCode, utcnow

logger = get_logger(__name__)

# The jobType this module registers on the queue (one vocabulary, DB-R2).
POSTAL_REFRESH_JOB_TYPE: Final = "postalReferenceRefresh"


@dataclass(frozen=True, slots=True)
class PostalReferenceRow:
    """One snapshot row from the upstream postal reference source."""

    postal_code_value: str
    city_name: str
    state_code: str


@dataclass(frozen=True, slots=True)
class PostalRefreshResult:
    """What one refresh did — logged and asserted on, never guessed from row counts."""

    inserted: int
    updated: int
    unchanged: int
    retired: int


def refresh_postal_reference(
    session: Session,
    rows: Iterable[PostalReferenceRow],
    *,
    country_code: str = "US",
    full_snapshot: bool = True,
) -> PostalRefreshResult:
    """Upsert a postal snapshot for one country; idempotent by construction.

    ``full_snapshot`` retires (soft-deletes) live codes absent from ``rows``;
    pass ``False`` for a partial correction feed that must not retire anything.
    Duplicate codes within ``rows`` collapse to the last occurrence.
    """
    existing = {
        row.postal_code_value: row
        for row in session.scalars(
            select(PostalCode)
            .where(PostalCode.deleted_at.is_(None))
            .where(PostalCode.country_code == country_code)
        )
    }
    inserted = updated = unchanged = retired = 0
    seen: set[str] = set()
    for row in rows:
        value = normalize_postal_code(row.postal_code_value)
        seen.add(value)
        current = existing.get(value)
        if current is None:
            session.add(
                PostalCode(
                    country_code=country_code,
                    postal_code_value=value,
                    city_name=row.city_name,
                    state_code=row.state_code,
                )
            )
            inserted += 1
        elif (current.city_name, current.state_code) != (row.city_name, row.state_code):
            current.city_name = row.city_name
            current.state_code = row.state_code
            updated += 1
        else:
            unchanged += 1
    if full_snapshot:
        now = utcnow()
        for value, current in existing.items():
            if value not in seen:
                current.deleted_at = now
                retired += 1
    session.flush()
    result = PostalRefreshResult(
        inserted=inserted, updated=updated, unchanged=unchanged, retired=retired
    )
    logger.info(
        "postal reference refreshed",
        extra={
            "context": {
                "countryCode": country_code,
                "inserted": inserted,
                "updated": updated,
                "unchanged": unchanged,
                "retired": retired,
            }
        },
    )
    return result


def postal_reference_refresh_job(session: Session, job: BackgroundJob) -> JobOutcome | None:
    """The queue handler for :data:`POSTAL_REFRESH_JOB_TYPE`.

    Payload contract (wire names): ``countryCode`` (default "US"),
    ``fullSnapshot`` (default true), and ``rows`` — a list of
    ``{"postalCode", "city", "state"}`` objects. A malformed payload is a
    :class:`PermanentJobError`: retrying re-reads the same document.
    """
    payload = job.job_payload
    try:
        rows = [
            PostalReferenceRow(
                postal_code_value=str(entry["postalCode"]),
                city_name=str(entry["city"]),
                state_code=str(entry["state"]),
            )
            for entry in payload["rows"]
        ]
    except (KeyError, TypeError) as exc:
        raise PermanentJobError(f"malformed postal refresh payload: {exc}") from exc
    refresh_postal_reference(
        session,
        rows,
        country_code=str(payload.get("countryCode", "US")),
        full_snapshot=bool(payload.get("fullSnapshot", True)),
    )
    return None
