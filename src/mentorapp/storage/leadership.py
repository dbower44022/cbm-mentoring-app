"""Leadership reporting reads: live-data aggregates over the leadership span (WTK-171/184).

The dashboards leadership asked for — engagement counts by status, sessions
held per period, per-mentor activity — derived from LIVE rows at read time,
exactly the triage stance (:mod:`mentorapp.storage.triage`): every number is
derivable, never hand-maintained, and no reporting table or BI layer is
invented. Each read is a single SELECT over the generated read views (DB-S9
— soft-deleted rows are already excluded, choice labels already joined),
executed through the same validated admin-SQL path production data sources
run under. The span is the whole org (leadership sees across mentors —
including unassigned engagements), so the sources are UNSCOPED; who may call
them is the API layer's grant decision, not a WHERE clause here.

The per-period read buckets in Python rather than SQL: month truncation has
no dialect-portable SQL spelling (SQLite ``strftime`` vs Postgres
``to_char``), and the one canonical bucketing function beats two SQL
variants that could drift. Periods are calendar months in UTC — the store's
own zone.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime
from typing import Any, Final

from sqlalchemy.orm import Session

from mentorapp.observability import get_logger
from mentorapp.storage.adminsql import AdminSqlSource, execute_admin_sql
from mentorapp.storage.base import as_utc, utcnow

log = get_logger(__name__)

# Engagement counts by status over every live engagement. The decoded LABEL
# is the grouping key (the triage precedent: admin SQL reads views only, and
# the label is the status's one decoded form on vwEngagement); a null label
# is the carried-over "status not set yet" bucket, reported honestly.
_STATUS_COUNTS_SQL: Final = (
    'SELECT e."engagementStatusLabel" AS "engagementStatusLabel",\n'
    '       COUNT(*) AS "engagementCount"\n'
    'FROM "vwEngagement" e\n'
    'GROUP BY e."engagementStatusLabel"\n'
    'ORDER BY COUNT(*) DESC, e."engagementStatusLabel"'
)

# Every live session's start time — the Python bucketing's input. Deliberately
# no WHERE: past/future is decided against the caller's ``now`` in one place.
_SESSION_TIMES_SQL: Final = 'SELECT s."scheduledAt" AS "scheduledAt"\nFROM "vwSession" s'

# Per-mentor activity: engagement count, sessions held, last session held.
# CURRENT_TIMESTAMP splits held from upcoming on both dialects (the triage
# rollup's exact device). INNER join on engagements: a mentor with no
# engagements has no activity row — leadership reads pairings, not the
# mentor roster (that is the CRM's).
_MENTOR_ACTIVITY_SQL: Final = (
    'SELECT m."crmMentorID" AS "crmMentorID",\n'
    '       m."userID" AS "userID",\n'
    '       COUNT(DISTINCT e."engagementID") AS "engagementCount",\n'
    '       COUNT(CASE WHEN s."scheduledAt" <= CURRENT_TIMESTAMP'
    ' THEN s."sessionID" END) AS "sessionsHeld",\n'
    '       MAX(CASE WHEN s."scheduledAt" <= CURRENT_TIMESTAMP'
    ' THEN s."scheduledAt" END) AS "lastSessionAt"\n'
    'FROM "vwCrmMentorRef" m\n'
    'JOIN "vwEngagement" e ON e."crmMentorRefID" = m."crmMentorRefID"\n'
    'LEFT JOIN "vwSession" s ON s."engagementID" = e."engagementID"\n'
    'GROUP BY m."crmMentorID", m."userID"\n'
    'ORDER BY m."crmMentorID"'
)


def _rows(session: Session, key: str, sql: str, current_user_id: uuid.UUID) -> list[dict]:
    """One unscoped report read through the validated admin-SQL executor."""
    source = AdminSqlSource(data_source_key=key, sql_text=sql, user_scoped_flag=False)
    return execute_admin_sql(session, source, current_user_id=current_user_id)


def engagement_status_counts(
    session: Session, *, current_user_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Live engagement counts by status label, largest bucket first."""
    rows = _rows(session, "leadershipStatusCounts", _STATUS_COUNTS_SQL, current_user_id)
    log.info(
        "leadership status counts read",
        extra={"context": {"bucketCount": len(rows)}},
    )
    return rows


def _as_datetime(value: Any) -> datetime:
    # Raw text SQL hands SQLite datetimes back as ISO strings while Postgres
    # answers datetime objects — one normalizer, then the one UTC rule.
    if isinstance(value, str):
        return as_utc(datetime.fromisoformat(value))
    return as_utc(value)


def sessions_held_per_period(
    session: Session, *, current_user_id: uuid.UUID, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Sessions HELD per calendar month (UTC), oldest period first.

    Held = live sessions whose start has passed (a cancelled session is a
    soft delete and left the view already; a future session is not yet
    news). Period keys are ``YYYY-MM`` — a stable, sortable wire form the
    dashlet renders directly.
    """
    now = now or utcnow()
    rows = _rows(session, "leadershipSessionTimes", _SESSION_TIMES_SQL, current_user_id)
    held = Counter(
        f"{when:%Y-%m}" for row in rows if (when := _as_datetime(row["scheduledAt"])) <= now
    )
    periods = [
        {"period": period, "sessionsHeld": count} for period, count in sorted(held.items())
    ]
    log.info(
        "leadership session activity read",
        extra={"context": {"periodCount": len(periods), "sessionsHeld": sum(held.values())}},
    )
    return periods


def mentor_activity(session: Session, *, current_user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Per-mentor activity over live rows: engagements, sessions held, last held."""
    rows = _rows(session, "leadershipMentorActivity", _MENTOR_ACTIVITY_SQL, current_user_id)
    log.info(
        "leadership mentor activity read",
        extra={"context": {"mentorCount": len(rows)}},
    )
    return rows
