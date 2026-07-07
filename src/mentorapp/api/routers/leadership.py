"""``/leadership`` — the leadership reporting surface (WTK-171/184).

One composed read, ``GET /leadership/reports``, serving the three dashboard
blocks over LIVE rows at request time (:mod:`mentorapp.storage.leadership` —
the derivation's one canonical home): engagement counts by status, sessions
held per calendar month, and per-mentor activity. Each block is directly
dashlet-consumable — a dashlet is a view rendered small (layout standard),
so a leadership dashlet fetches this read and renders its block; no BI
layer, snapshot table, or second aggregation path is invented.

Who may read it is the REQ-006 grant boundary, not a role name check: the
reports aggregate exactly the across-mentors engagement span, so the grant
that admits a caller to that span — the ``leadershipEngagements`` data
source (:data:`~mentorapp.access.mentoring.DS_LEADERSHIP_ENGAGEMENTS`,
granted to Leadership only) — is the grant that admits them to its
aggregates. A mentor gets the standard 403 envelope; revoking the leadership
grant revokes the reports in the same act, with no second permission model
to sweep.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from mentorapp.access.grants import StoredGrantRegistry, authorize_data_source
from mentorapp.access.mentoring import DS_LEADERSHIP_ENGAGEMENTS
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.envelope import Envelope, ok

# The one role seam, deliberately imported (the mentoring-router stance):
# wiring and tests bind the session-roles provider exactly once.
from mentorapp.api.routers.workprocess import RoleSource, get_role_source
from mentorapp.observability import get_logger
from mentorapp.storage import (
    engagement_status_counts,
    mentor_activity,
    sessions_held_per_period,
)

log = get_logger(__name__)

router = APIRouter()

_SessionDep = Annotated[Session, Depends(get_session)]
_UserDep = Annotated[uuid.UUID, Depends(get_current_user_id)]
_RolesDep = Annotated[RoleSource, Depends(get_role_source)]


@router.get("/leadership/reports")
def get_leadership_reports(
    session: _SessionDep, user_id: _UserDep, roles: _RolesDep
) -> Envelope:
    """The leadership dashboard blocks, derived live (WTK-171/184).

    ``data.engagementsByStatus`` — live engagement counts per status label,
    largest bucket first (a null label is the carried-over "not set yet"
    bucket, reported honestly). ``data.sessionsHeldByMonth`` — sessions held
    per UTC calendar month, oldest first. ``data.mentorActivity`` — per
    paired mentor: engagement count, sessions held, last session held.
    403 through the one grant boundary for callers without the leadership
    engagement span; 401 without a live session reference (FND-909 D9).
    """
    authorize_data_source(
        StoredGrantRegistry(session),
        data_source_key=DS_LEADERSHIP_ENGAGEMENTS,
        user_id=user_id,
        user_roles=roles.user_roles(user_id),
    )
    by_status = engagement_status_counts(session, current_user_id=user_id)
    by_month = sessions_held_per_period(session, current_user_id=user_id)
    activity = mentor_activity(session, current_user_id=user_id)
    log.info(
        "leadership reports served",
        extra={
            "context": {
                "userId": str(user_id),
                "statusBuckets": len(by_status),
                "periods": len(by_month),
                "mentors": len(activity),
            }
        },
    )
    return ok(
        data={
            "engagementsByStatus": by_status,
            "sessionsHeldByMonth": by_month,
            "mentorActivity": activity,
        },
        meta={
            "totalEngagements": sum(row["engagementCount"] for row in by_status),
            "totalSessionsHeld": sum(row["sessionsHeld"] for row in by_month),
        },
    )
