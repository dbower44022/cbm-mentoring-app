"""The engagement triage read: REQ-072's columns as server-side truth (WTK-164/167).

REQ-072 fixes the staff/mentor triage row — engagement name, status, primary
contact name and email, last and next session dates, total sessions — and
requires every column to be DERIVABLE, never hand-maintained. This module is
the one canonical home of that derivation: a single SELECT over the
generated read views (``vwEngagement``/``vwSession``/``vwCrmMentorRef``,
DB-S9 — never base tables) that both seeded engagement data sources embed
(:mod:`mentorapp.access.mentoring`) and
:func:`engagement_triage_rows` executes for server-side consumers, through
the same validated admin-SQL executor production sources run under. One SQL
body, two grants — the mentor-scoped and leadership variants differ only in
the REQ-019 user scoping, so the columns can never drift apart.

Scoping (WTK-167/REQ-019): the mentor variant joins the mentor anchor and
binds its ``userID`` pairing column server-side to ``:currentUserID`` — the
executor injects the session user and rejects a caller-supplied one, so a
mentor's rows are confined to their own engagements by construction. The
leadership variant omits the join entirely: leadership sees across mentors,
including engagements with no mentor assigned yet (Pending Acceptance).

Session aggregates read the LIVE session rows only (the views bake in the
soft-delete filter): a cancelled session is a soft delete, so it leaves the
counts and the last/next dates the moment it is cancelled — no status
decoding in SQL, no stale triage.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy.orm import Session

from mentorapp.observability import get_logger
from mentorapp.storage.adminsql import AdminSqlSource, execute_admin_sql

log = get_logger(__name__)

# The REQ-072 triage columns, in requirement order, as the SELECT aliases
# them. ``openActionItems`` joined the set with the PI-010 surfaces ruling —
# the triage priority ends on open action items, so the read must derive the
# signal (and a grid may badge it). The scoped variant additionally exposes
# the mentor pairing column (``userID``) the REQ-019 row filter binds.
ENGAGEMENT_TRIAGE_COLUMNS: Final[tuple[str, ...]] = (
    "engagementID",
    "engagementName",
    "engagementStatusLabel",
    "primaryContactName",
    "primaryContactEmail",
    "lastSessionAt",
    "nextSessionAt",
    "totalSessions",
    "openActionItems",
)

# Per-engagement session rollup over the live rows the view serves.
# CURRENT_TIMESTAMP splits past from future on both dialects (Postgres
# compares timestamptz natively; SQLite compares the ISO text forms, which
# share the "YYYY-MM-DD HH:MM:SS" prefix ordering).
_SESSION_ROLLUP: Final = (
    'SELECT s."engagementID" AS "engagementID",\n'
    '       MAX(CASE WHEN s."scheduledAt" <= CURRENT_TIMESTAMP'
    ' THEN s."scheduledAt" END) AS "lastSessionAt",\n'
    '       MIN(CASE WHEN s."scheduledAt" > CURRENT_TIMESTAMP'
    ' THEN s."scheduledAt" END) AS "nextSessionAt",\n'
    '       COUNT(*) AS "totalSessions",\n'
    # Open action items are REQ-082 rich text ON sessions (never task
    # records), so "any live session carries action items" IS the signal.
    '       MAX(CASE WHEN s."actionItems" IS NOT NULL'
    ' AND s."actionItems" <> \'\' THEN 1 ELSE 0 END) AS "openActionItems"\n'
    'FROM "vwSession" s\n'
    'GROUP BY s."engagementID"'
)

# The triage ORDER (PI-010 surfaces ruling on REQ-072): pending acceptances
# first, then imminent sessions (soonest next session; none sorts last),
# then engagements with open action items, then name as the stable tail.
# The status test reads the decoded LABEL because admin-SQL sources may only
# read views, and the option-value table has no view — the label is the
# status's one decoded form on vwEngagement.
_TRIAGE_ORDER: Final = (
    "\nORDER BY CASE WHEN e.\"engagementStatusLabel\" = 'Pending Acceptance'"
    " THEN 0 ELSE 1 END,\n"
    '         CASE WHEN r."nextSessionAt" IS NULL THEN 1 ELSE 0 END,\n'
    '         r."nextSessionAt",\n'
    '         COALESCE(r."openActionItems", 0) DESC,\n'
    '         e."engagementName"'
)


def engagement_triage_sql(*, mentor_scoped: bool) -> str:
    """The one REQ-072 SELECT, in its mentor-scoped or leadership form.

    Passes :func:`~mentorapp.storage.adminsql.validate_admin_sql` by
    construction (single SELECT over views, no comment tokens), so it can be
    stored verbatim as a ``dataSource`` body. The scoped form references
    ``:currentUserID`` exactly once — on the mentor anchor's ``userID``
    pairing column — which the executor binds server-side.
    """
    select_columns = [
        'e."engagementID"',
        'e."engagementName"',
        'e."engagementStatusLabel"',
        'e."primaryContactName"',
        'e."primaryContactEmail"',
        'r."lastSessionAt"',
        'r."nextSessionAt"',
        'COALESCE(r."totalSessions", 0) AS "totalSessions"',
        'COALESCE(r."openActionItems", 0) AS "openActionItems"',
    ]
    joins = [f'LEFT JOIN ({_SESSION_ROLLUP}) r ON r."engagementID" = e."engagementID"']
    where = ""
    if mentor_scoped:
        select_columns.append('m."userID"')
        # INNER join on purpose: an engagement with no assigned mentor is
        # nobody's row in the mentor variant; leadership sees it instead.
        joins.insert(0, 'JOIN "vwCrmMentorRef" m ON m."crmMentorRefID" = e."crmMentorRefID"')
        where = '\nWHERE m."userID" = :currentUserID'
    return (
        "SELECT "
        + ",\n       ".join(select_columns)
        + '\nFROM "vwEngagement" e\n'
        + "\n".join(joins)
        + where
        + _TRIAGE_ORDER
    )


def engagement_triage_rows(
    session: Session, *, current_user_id: uuid.UUID, mentor_scoped: bool = True
) -> list[dict[str, Any]]:
    """Execute the triage read and return its rows — server-side truth.

    Runs through :func:`~mentorapp.storage.adminsql.execute_admin_sql`, the
    same validated, role-isolated, user-injected path the seeded data
    sources take, so tests and server-side consumers exercise exactly what
    production serves. ``current_user_id`` is the session user; it scopes
    the mentor variant and is ignored by the leadership variant (which
    never references it).
    """
    source = AdminSqlSource(
        data_source_key="engagementTriage",
        sql_text=engagement_triage_sql(mentor_scoped=mentor_scoped),
        user_scoped_flag=mentor_scoped,
    )
    rows = execute_admin_sql(session, source, current_user_id=current_user_id)
    log.info(
        "engagement triage read",
        extra={"context": {"mentorScoped": mentor_scoped, "rowCount": len(rows)}},
    )
    return rows
