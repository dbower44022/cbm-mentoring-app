"""The mentor-facing engagement surfaces (PI-010: WTK-168/169/177/178/179/183).

The read/write API behind the approved prototype's screens:

- ``GET /engagements`` — the caller's engagement picker list (mentor-scoped;
  leadership spans mentors, REQ-019's rule restated over the ORM).
- ``GET /engagements/{id}/rollup`` — the ONE aggregation read (WTK-183)
  behind both the docked engagement preview (REQ-073/074/088) and the
  session prep surface (REQ-081): every session's notes and action items
  newest-first, engagement history/stats, and the client/company/contact
  references the preview offers as click-through pop-ups.
- ``POST /engagements/{id}/lifecycle`` — the REQ-075 status transitions
  (accept / decline / hold / dormant) in the stakeholder-confirmed
  vocabulary. Decline is a status change ONLY (DEC-071 — nothing is
  deleted; staff sees and reassigns). Invalid transitions refuse in the
  educate voice (what happened → why → what next), never a bare 4xx.
  Accepting answers the REQ-076 next steps: send the introduction email,
  schedule the first session.
- ``POST /engagements/{id}/sessions`` / ``PATCH /sessions/{id}`` — session
  create and the REQ-082 entry write (rich-text notes, bulleted action
  items, the REQ-079 conference link), riding the ONE write engine
  (validation, history, feed, ``rowVersion`` — never re-implemented here).
- ``GET /email/templates`` / ``POST /email/send`` /
  ``POST /resources/{id}/share`` — REQ-076/077 templated outbound email and
  the REQ-084 share-a-resource flow. The send POST answers the MERGED
  preview when ``confirmed`` is false and hands the identical merge to the
  transport seam when true — preview-before-send with one merge path.

Scoping: every engagement-addressed verb resolves through
:func:`_scoped_engagement` — the caller is the engagement's paired mentor
(``crmMentorRef.userID``) or holds the Leadership role; anyone else gets the
same 404 as an engagement that never existed, so engagement ids cannot be
probed (the workprocess ``_own_run`` precedent). Roles ride the one
fail-loud role seam (:func:`~mentorapp.api.routers.workprocess.get_role_source`),
deliberately imported rather than re-declared so wiring and tests bind the
session-roles provider exactly once.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.access.mentoring import (
    DS_LEADERSHIP_ENGAGEMENTS,
    DS_MENTOR_ENGAGEMENTS,
    DS_MENTOR_SESSIONS,
    LEADERSHIP_ROLE,
)
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.envelope import Envelope, field_error, ok
from mentorapp.api.errors import ApiValidationError, RecordNotFoundError
from mentorapp.api.records import serialize_record

# The one role seam (the shell-catalog pattern): declared by the workprocess
# router, bound once by wiring/tests — re-declaring it here would fork the
# session-roles provider into two keys that could disagree.
from mentorapp.api.routers.workprocess import RoleSource, get_role_source
from mentorapp.api.write_engine import create_record, partial_update
from mentorapp.automation.email_outbound import (
    CatalogTemplateSource,
    EmailTemplateSource,
    EmailTransport,
    LoggedEmailTransport,
    MergeFieldError,
    OutboundEmail,
    merge_template,
)
from mentorapp.observability import get_logger
from mentorapp.storage import (
    ENGAGEMENT_STATUS_OPTION_SET,
    SESSION_STATUS_OPTION_SET,
    AppUser,
    Engagement,
    MentoringSession,
    OptionSet,
    OptionValue,
    Resource,
    as_utc,
    utcnow,
)
from mentorapp.ui.auth_flows import EducateMessage

log = get_logger(__name__)

router = APIRouter()

_ENGAGEMENT_ENTITY: Final = "engagement"
_SESSION_ENTITY: Final = "session"
_RESOURCE_ENTITY: Final = "resource"

# Stable machine-readable codes (clients switch on these; adding is additive).
CODE_UNKNOWN_LIFECYCLE_TRANSITION = "unknownLifecycleTransition"
CODE_INVALID_LIFECYCLE_TRANSITION = "invalidLifecycleTransition"
CODE_UNKNOWN_EMAIL_TEMPLATE = "unknownEmailTemplate"
CODE_MISSING_MERGE_FIELDS = "missingMergeFields"
CODE_NO_CONTACT_EMAIL = "noContactEmail"
CODE_UNKNOWN_SESSION_STATUS = "unknownSessionStatus"

# The grids a lifecycle flip changes — the client's refresh list, exactly the
# workprocess commit's meta.affectedDataSourceKeys shape.
_ENGAGEMENT_SOURCE_KEYS: Final = (DS_MENTOR_ENGAGEMENTS, DS_LEADERSHIP_ENGAGEMENTS)


# --- The REQ-075 lifecycle vocabulary as transitions ---------------------------------


@dataclass(frozen=True)
class LifecycleTransition:
    """One sanctioned status transition (REQ-075) with its own educate voice.

    ``from_statuses`` are option-value NAMES (the stable identifiers;
    labels are admin data and may be relabeled). ``refusal_what_next`` is
    the transition-specific "what next" an invalid invocation educates with
    — the never-hide rule means the action always ran, so the refusal must
    teach, not scold.
    """

    transition_key: str
    label: str
    from_statuses: tuple[str, ...]
    to_status: str
    confirmation: str
    refusal_what_next: str
    next_steps: tuple[dict[str, str], ...] = ()


LIFECYCLE_TRANSITIONS: Final[dict[str, LifecycleTransition]] = {
    transition.transition_key: transition
    for transition in (
        LifecycleTransition(
            transition_key="accept",
            label="Accept Assignment",
            from_statuses=("pendingAcceptance",),
            to_status="assigned",
            confirmation=(
                "'{engagementName}' accepted — its status is now Assigned. "
                "Next: send the introduction email and schedule the first session."
            ),
            refusal_what_next=(
                "Select an engagement in Pending Acceptance (they lead your "
                "triage view), or use an action appropriate to this status."
            ),
            # REQ-076: accepting surfaces the post-acceptance first steps in
            # place — the client renders these as the next-step affordances.
            next_steps=(
                {
                    "key": "sendIntroEmail",
                    "label": "Send the introduction email",
                    "templateKey": "mentorIntroduction",
                },
                {"key": "scheduleFirstSession", "label": "Schedule the first session"},
            ),
        ),
        LifecycleTransition(
            transition_key="decline",
            label="Decline Assignment",
            from_statuses=("pendingAcceptance",),
            to_status="assignmentDeclined",
            # DEC-071: a decline is a status change only — the record
            # survives untouched for staff to see and reassign.
            confirmation=(
                "'{engagementName}' declined — a status change only; nothing "
                "is deleted, and staff will see it and reassign."
            ),
            refusal_what_next=(
                "Only a Pending Acceptance assignment can be declined. If you "
                "need to step away from an engagement already underway, "
                "contact the program office to reassign it."
            ),
        ),
        LifecycleTransition(
            transition_key="hold",
            label="Put On Hold",
            from_statuses=("active", "assigned"),
            to_status="onHold",
            confirmation=(
                "'{engagementName}' is now On Hold — the client requested a "
                "pause. If the client has simply stopped responding, Dormant "
                "is the truthful status instead."
            ),
            refusal_what_next=(
                "On Hold applies to an engagement that is underway (Active or "
                "Assigned). Pick one of those, or use the action this status "
                "calls for."
            ),
        ),
        LifecycleTransition(
            transition_key="dormant",
            label="Mark Dormant",
            from_statuses=("active", "assigned", "onHold"),
            to_status="dormant",
            confirmation=(
                "'{engagementName}' is now Dormant — the client has gone "
                "quiet. A re-engagement check-in email is available from the "
                "templated email action."
            ),
            refusal_what_next=(
                "Dormant applies to an engagement that was underway (Active, "
                "Assigned, or On Hold). Pick one of those, or use the action "
                "this status calls for."
            ),
        ),
    )
}


# --- Provider seams -------------------------------------------------------------------

# Module-level defaults are the sanctioned dev state (see email_outbound):
# templates come from the source-controlled catalog, sends land in the logged
# transport. Deployment wiring overrides these dependencies to bind a stored
# template entity and a real SMTP/provider without touching any endpoint.
_TEMPLATE_SOURCE = CatalogTemplateSource()
_DEV_TRANSPORT = LoggedEmailTransport()


def get_email_templates() -> EmailTemplateSource:
    """Provide the staff-maintained template list (REQ-077)."""
    return _TEMPLATE_SOURCE


def get_email_transport() -> EmailTransport:
    """Provide the outbound delivery seam (REQ-076) — dev default logs, never sends."""
    return _DEV_TRANSPORT


_SessionDep = Annotated[Session, Depends(get_session)]
_UserDep = Annotated[uuid.UUID, Depends(get_current_user_id)]
_RolesDep = Annotated[RoleSource, Depends(get_role_source)]
_TemplatesDep = Annotated[EmailTemplateSource, Depends(get_email_templates)]
_TransportDep = Annotated[EmailTransport, Depends(get_email_transport)]


# --- Option-set reads (DB-S7: names are stable, labels are admin data) ----------------


def _option_values(session: Session, option_set_name: str) -> list[OptionValue]:
    return list(
        session.scalars(
            select(OptionValue)
            .join(OptionSet, OptionSet.option_set_id == OptionValue.option_set_id)
            .where(
                OptionSet.option_set_name == option_set_name,
                OptionSet.deleted_at.is_(None),
                OptionValue.deleted_at.is_(None),
            )
        )
    )


def _status_vocabulary(
    session: Session, option_set_name: str
) -> tuple[dict[uuid.UUID, OptionValue], dict[str, OptionValue]]:
    """The set's live values keyed both ways: by id (records) and name (code)."""
    values = _option_values(session, option_set_name)
    return (
        {value.option_value_id: value for value in values},
        {value.option_value_name: value for value in values},
    )


# --- Scoping (REQ-019 over the ORM) ---------------------------------------------------


def _is_leadership(roles: RoleSource, user_id: uuid.UUID) -> bool:
    return LEADERSHIP_ROLE in roles.user_roles(user_id)


def _scoped_engagement(
    session: Session, engagement_id: uuid.UUID, user_id: uuid.UUID, roles: RoleSource
) -> Engagement:
    """The caller's live engagement, or the uniform 404.

    The paired mentor and leadership are the two sanctioned readers; every
    other caller gets exactly the answer an unknown id gets, so engagement
    ids cannot be probed for existence.
    """
    engagement = session.get(Engagement, engagement_id)
    if engagement is None or engagement.deleted_at is not None:
        raise RecordNotFoundError(_ENGAGEMENT_ENTITY, str(engagement_id))
    mentor = engagement.crm_mentor_ref
    is_own = mentor is not None and mentor.user_id == user_id
    if not is_own and not _is_leadership(roles, user_id):
        raise RecordNotFoundError(_ENGAGEMENT_ENTITY, str(engagement_id))
    return engagement


# --- Payload builders -------------------------------------------------------------------


def _engagement_payload(
    engagement: Engagement, by_id: dict[uuid.UUID, OptionValue]
) -> dict[str, Any]:
    status = by_id.get(engagement.engagement_status) if engagement.engagement_status else None
    return {
        "engagementID": engagement.engagement_id,
        "engagementName": engagement.engagement_name,
        "engagementStatus": status.option_value_name if status else None,
        "engagementStatusLabel": status.option_value_label if status else None,
        "engagementSummary": engagement.engagement_summary,
        "primaryContactName": engagement.primary_contact_name,
        "primaryContactEmail": engagement.primary_contact_email,
        "primaryContactCrmID": engagement.primary_contact_crm_id,
        "crmEngagementID": engagement.crm_engagement_id,
        "rowVersion": engagement.row_version,
    }


def _session_entry(
    record: MentoringSession, by_id: dict[uuid.UUID, OptionValue]
) -> dict[str, Any]:
    status = by_id.get(record.session_status) if record.session_status else None
    return {
        "sessionID": record.session_id,
        "scheduledAt": record.scheduled_at,
        "sessionStatus": status.option_value_name if status else None,
        "sessionStatusLabel": status.option_value_label if status else None,
        "conferenceLink": record.conference_link,
        "sessionNotes": record.session_notes,
        "actionItems": record.action_items,
        "rowVersion": record.row_version,
    }


def _live_sessions_newest_first(engagement: Engagement) -> list[MentoringSession]:
    # as_utc on every temporal compare: SQLite hands datetimes back naive,
    # Postgres aware — the one normalizer keeps ordering dialect-proof.
    live = [s for s in engagement.sessions if s.deleted_at is None]
    return sorted(live, key=lambda s: as_utc(s.scheduled_at), reverse=True)


# --- The engagement list (picker read) -------------------------------------------------


@router.get("/engagements")
def list_engagements(session: _SessionDep, user_id: _UserDep, roles: _RolesDep) -> Envelope:
    """The caller's engagements, name-ordered — the picker/list read.

    The REQ-019 rule restated over the ORM: a mentor sees the engagements
    paired to them; leadership sees every live engagement (including
    unassigned Pending Acceptance ones). Grid surfaces stay on the seeded
    data sources — this read exists for pickers and flows that address an
    engagement, not to replace the triage grid.
    """
    by_id, _ = _status_vocabulary(session, ENGAGEMENT_STATUS_OPTION_SET)
    stmt = select(Engagement).where(Engagement.deleted_at.is_(None))
    rows = session.scalars(stmt).all()
    if not _is_leadership(roles, user_id):
        rows = [
            row
            for row in rows
            if row.crm_mentor_ref is not None and row.crm_mentor_ref.user_id == user_id
        ]
    rows.sort(key=lambda row: (row.engagement_name or "").lower())
    return ok(
        data=[_engagement_payload(row, by_id) for row in rows],
        meta={"totalCount": len(rows)},
    )


# --- The rollup read (WTK-183 — one aggregation for preview AND prep) ------------------


@router.get("/engagements/{engagement_id}/rollup")
def get_engagement_rollup(
    engagement_id: uuid.UUID, session: _SessionDep, user_id: _UserDep, roles: _RolesDep
) -> Envelope:
    """Everything the preview and prep surfaces render for one engagement.

    ``data.rollup`` is REQ-073/074's aggregation: every live session
    carrying notes or action items, NEWEST FIRST, so the mentor never opens
    sessions one by one to find them. ``data.sessions`` is the full live
    session history (same order) for the sessions list and prep navigation.
    ``data.stats`` derives from the live rows exactly as the triage read
    does (time splits past/future; a cancelled session is a soft delete and
    leaves the aggregates immediately). ``data.client`` and
    ``data.contacts`` carry the references the preview offers as
    click-through pop-ups (REQ-074).
    """
    engagement = _scoped_engagement(session, engagement_id, user_id, roles)
    engagement_status_by_id, _ = _status_vocabulary(session, ENGAGEMENT_STATUS_OPTION_SET)
    session_status_by_id, _ = _status_vocabulary(session, SESSION_STATUS_OPTION_SET)

    sessions_newest_first = _live_sessions_newest_first(engagement)
    entries = [_session_entry(s, session_status_by_id) for s in sessions_newest_first]
    rollup = [e for e in entries if e["sessionNotes"] or e["actionItems"]]

    now = utcnow()
    past = [
        as_utc(s.scheduled_at) for s in sessions_newest_first if as_utc(s.scheduled_at) <= now
    ]
    future = [
        as_utc(s.scheduled_at) for s in sessions_newest_first if as_utc(s.scheduled_at) > now
    ]

    client = (
        engagement.client
        if engagement.client and engagement.client.deleted_at is None
        else None
    )
    company = client.crm_company_ref if client is not None else None
    contacts = (
        [
            {
                "contactName": engagement.primary_contact_name,
                "contactEmail": engagement.primary_contact_email,
                "crmContactID": engagement.primary_contact_crm_id,
            }
        ]
        if engagement.primary_contact_name
        else []
    )

    log.info(
        "engagement rollup served",
        extra={
            "context": {
                "userId": str(user_id),
                "engagementID": str(engagement_id),
                "rollupCount": len(rollup),
                "sessionCount": len(entries),
            }
        },
    )
    return ok(
        data={
            "engagement": _engagement_payload(engagement, engagement_status_by_id),
            "client": (
                {
                    "clientID": client.client_id,
                    "clientSince": client.client_since,
                    "clientProgram": client.client_program,
                    "clientReferralSource": client.client_referral_source,
                    "clientStage": client.client_stage,
                    "crmCompanyRefID": company.crm_company_ref_id if company else None,
                    "crmCompanyID": company.crm_company_id if company else None,
                }
                if client is not None
                else None
            ),
            "contacts": contacts,
            "stats": {
                "totalSessions": len(sessions_newest_first),
                "heldSessions": len(past),
                "firstSessionAt": min(past) if past else None,
                "lastSessionAt": max(past) if past else None,
                "nextSessionAt": min(future) if future else None,
            },
            "rollup": rollup,
            "sessions": entries,
        },
        meta={"rollupCount": len(rollup), "sessionCount": len(entries)},
    )


# --- Lifecycle (WTK-183, REQ-075/REQ-076, DEC-071) --------------------------------------


class LifecycleBody(BaseModel):
    """POST body: which transition, guarded by the record's ``rowVersion``."""

    model_config = ConfigDict(extra="forbid")

    transition: str = Field(min_length=1)
    row_version: int = Field(alias="rowVersion")


def _lifecycle_refusal(
    transition: LifecycleTransition,
    engagement: Engagement,
    current_status: OptionValue | None,
    by_name: dict[str, OptionValue],
) -> ApiValidationError:
    """The educate-voice 422 for a transition this status does not allow."""
    name = engagement.engagement_name or "this engagement"
    if current_status is None:
        why = (
            "its status has not been set yet — carried-over engagements are "
            "completed by staff before lifecycle actions apply."
        )
    else:
        allowed = " or ".join(
            by_name[status].option_value_label
            for status in transition.from_statuses
            if status in by_name
        )
        why = (
            f"its status is '{current_status.option_value_label}' — "
            f"{transition.label} applies only to an engagement in {allowed}."
        )
    message = EducateMessage(
        what_happened=f"{transition.label} ran on '{name}' and didn't apply.",
        why=why,
        what_next=transition.refusal_what_next,
    )
    return ApiValidationError(
        [
            field_error(
                "transition",
                CODE_INVALID_LIFECYCLE_TRANSITION,
                f"{message.what_happened} {message.why} {message.what_next}",
            )
        ]
    )


@router.post("/engagements/{engagement_id}/lifecycle")
def post_engagement_lifecycle(
    engagement_id: uuid.UUID,
    body: LifecycleBody,
    session: _SessionDep,
    user_id: _UserDep,
    roles: _RolesDep,
) -> Envelope:
    """Apply one REQ-075 transition — accept / decline / hold / dormant.

    The status flip rides :func:`partial_update` (the one write engine), so
    it is option-validated, history-tracked, fed to the change feed, and
    ``rowVersion``-guarded like every other write. An unknown transition
    name is 422 ``unknownLifecycleTransition``; a transition the current
    status does not allow is 422 ``invalidLifecycleTransition`` in the
    educate voice; a stale ``rowVersion`` is the standard 409 with the
    current record. Success confirms in words and names the engagement
    grids to refresh (``meta.affectedDataSourceKeys``); accepting also
    carries the REQ-076 next steps.
    """
    engagement = _scoped_engagement(session, engagement_id, user_id, roles)
    transition = LIFECYCLE_TRANSITIONS.get(body.transition)
    if transition is None:
        raise ApiValidationError(
            [
                field_error(
                    "transition",
                    CODE_UNKNOWN_LIFECYCLE_TRANSITION,
                    f"'{body.transition}' is not an engagement lifecycle "
                    f"transition; the transitions are "
                    f"{', '.join(sorted(LIFECYCLE_TRANSITIONS))}.",
                )
            ]
        )
    by_id, by_name = _status_vocabulary(session, ENGAGEMENT_STATUS_OPTION_SET)
    current = by_id.get(engagement.engagement_status) if engagement.engagement_status else None
    if current is None or current.option_value_name not in transition.from_statuses:
        raise _lifecycle_refusal(transition, engagement, current, by_name)

    partial_update(
        session,
        engagement,
        _ENGAGEMENT_ENTITY,
        {"engagementStatus": by_name[transition.to_status].option_value_id},
        row_version=body.row_version,
        acting_user_id=user_id,
    )
    session.commit()
    log.info(
        "engagement lifecycle transition applied",
        extra={
            "context": {
                "userId": str(user_id),
                "engagementID": str(engagement_id),
                "transition": transition.transition_key,
                "fromStatus": current.option_value_name,
                "toStatus": transition.to_status,
            }
        },
    )
    name = engagement.engagement_name or "This engagement"
    return ok(
        data={
            "engagement": _engagement_payload(engagement, by_id),
            "transition": transition.transition_key,
            "confirmation": transition.confirmation.format(engagementName=name),
            "nextSteps": [dict(step) for step in transition.next_steps],
        },
        meta={"affectedDataSourceKeys": list(_ENGAGEMENT_SOURCE_KEYS)},
    )


# --- Sessions (WTK-168/177, REQ-079/REQ-082) --------------------------------------------


class SessionCreateBody(BaseModel):
    """POST body: when the session is, and optionally how to join it."""

    model_config = ConfigDict(extra="forbid")

    scheduled_at: datetime = Field(alias="scheduledAt")
    conference_link: str | None = Field(default=None, alias="conferenceLink", max_length=2000)


@router.post("/engagements/{engagement_id}/sessions")
def post_session_create(
    engagement_id: uuid.UUID,
    body: SessionCreateBody,
    session: _SessionDep,
    user_id: _UserDep,
    roles: _RolesDep,
) -> Envelope:
    """Schedule one session of the caller's engagement (REQ-078's manual core).

    Created ``scheduled`` with an optional pasted conference link (REQ-079;
    the REQ-080 auto-created org meeting is a later integration — the link
    column is where it will land). Rides :func:`create_record` so registry
    validation, audit stamping, and the feed apply exactly as everywhere.
    """
    engagement = _scoped_engagement(session, engagement_id, user_id, roles)
    _, by_name = _status_vocabulary(session, SESSION_STATUS_OPTION_SET)
    record = create_record(
        session,
        MentoringSession,
        _SESSION_ENTITY,
        {
            "engagementID": engagement.engagement_id,
            "scheduledAt": body.scheduled_at,
            "sessionStatus": by_name["scheduled"].option_value_id,
            "conferenceLink": body.conference_link,
        },
        acting_user_id=user_id,
    )
    session.commit()
    log.info(
        "session scheduled",
        extra={
            "context": {
                "userId": str(user_id),
                "engagementID": str(engagement_id),
                "sessionID": str(record.session_id),
            }
        },
    )
    return ok(
        data=serialize_record(record),
        meta={"affectedDataSourceKeys": [DS_MENTOR_SESSIONS, *_ENGAGEMENT_SOURCE_KEYS]},
    )


class SessionPatchBody(BaseModel):
    """PATCH body: only the entry fields the prep surface writes (REQ-082).

    ``sessionNotes``/``actionItems`` carry the rich-text control's clean
    HTML; ``sessionStatus`` travels as the option-value NAME (the stable
    identifier) and is resolved to its ``optionValueID`` here.
    """

    model_config = ConfigDict(extra="forbid")

    row_version: int = Field(alias="rowVersion")
    session_notes: str | None = Field(default=None, alias="sessionNotes", max_length=4000)
    action_items: str | None = Field(default=None, alias="actionItems", max_length=4000)
    conference_link: str | None = Field(default=None, alias="conferenceLink", max_length=2000)
    session_status: str | None = Field(default=None, alias="sessionStatus")


@router.patch("/sessions/{session_id}")
def patch_session(
    session_id: uuid.UUID,
    body: SessionPatchBody,
    session: _SessionDep,
    user_id: _UserDep,
    roles: _RolesDep,
) -> Envelope:
    """The session entry write: notes, action items, link, status (REQ-082/079).

    Scoped through the session's engagement (the same uniform 404), guarded
    by ``rowVersion``, applied through the one write engine — only the
    fields actually sent travel, and unchanged values never bump the
    version. An unknown status name is 422 ``unknownSessionStatus``.
    """
    record = session.get(MentoringSession, session_id)
    if record is None or record.deleted_at is not None:
        raise RecordNotFoundError(_SESSION_ENTITY, str(session_id))
    _scoped_engagement(session, record.engagement_id, user_id, roles)

    sent = body.model_fields_set
    changes: dict[str, Any] = {}
    for attr, field_name in (
        ("session_notes", "sessionNotes"),
        ("action_items", "actionItems"),
        ("conference_link", "conferenceLink"),
    ):
        if attr in sent:
            changes[field_name] = getattr(body, attr)
    if "session_status" in sent and body.session_status is not None:
        _, by_name = _status_vocabulary(session, SESSION_STATUS_OPTION_SET)
        status = by_name.get(body.session_status)
        if status is None:
            raise ApiValidationError(
                [
                    field_error(
                        "sessionStatus",
                        CODE_UNKNOWN_SESSION_STATUS,
                        f"'{body.session_status}' is not a session status; "
                        f"the statuses are {', '.join(sorted(by_name))}.",
                    )
                ]
            )
        changes["sessionStatus"] = status.option_value_id

    partial_update(
        session,
        record,
        _SESSION_ENTITY,
        changes,
        row_version=body.row_version,
        acting_user_id=user_id,
    )
    session.commit()
    log.info(
        "session entry saved",
        extra={
            "context": {
                "userId": str(user_id),
                "sessionID": str(session_id),
                "fields": sorted(changes),
            }
        },
    )
    return ok(data=serialize_record(record))


# --- Templated outbound email (WTK-169/179, REQ-076/REQ-077) ----------------------------


def _mentor_name(session: Session, user_id: uuid.UUID) -> str:
    # The sender signs the email: the acting user's login name until a
    # display-name field exists on appUser (a column addition, not a redesign).
    user = session.get(AppUser, user_id)
    return user.username if user is not None else ""


def _engagement_merge_context(
    session: Session, engagement: Engagement, user_id: uuid.UUID
) -> dict[str, str]:
    return {
        "contactName": engagement.primary_contact_name or "",
        "contactEmail": engagement.primary_contact_email or "",
        "engagementName": engagement.engagement_name or "",
        "mentorName": _mentor_name(session, user_id),
    }


def _merged_or_refused(
    template_key: str, templates: EmailTemplateSource, context: dict[str, str]
) -> tuple[str, str]:
    template = templates.template(template_key)
    if template is None:
        raise ApiValidationError(
            [
                field_error(
                    "templateKey",
                    CODE_UNKNOWN_EMAIL_TEMPLATE,
                    f"'{template_key}' is not a template on the staff-"
                    f"maintained list; read GET /email/templates for the "
                    f"current list.",
                )
            ]
        )
    try:
        return merge_template(template, context)
    except MergeFieldError as missing:
        raise ApiValidationError(
            [
                field_error(
                    "templateKey",
                    CODE_MISSING_MERGE_FIELDS,
                    f"'{template.template_name}' needs "
                    f"{', '.join(sorted(missing.missing_fields))}, which this "
                    f"flow doesn't supply. Resource templates send from a "
                    f"resource's Share action; engagement templates need the "
                    f"engagement's contact and name completed.",
                )
            ]
        ) from missing


def _no_contact_refusal(engagement: Engagement) -> ApiValidationError:
    name = engagement.engagement_name or "this engagement"
    return ApiValidationError(
        [
            field_error(
                "engagementID",
                CODE_NO_CONTACT_EMAIL,
                f"'{name}' has no primary contact email on record, so there "
                f"is nobody to address. Staff completes the engagement's "
                f"contact details; ask the program office to add one.",
            )
        ]
    )


def _send_or_preview(
    engagement: Engagement,
    template_key: str,
    subject: str,
    body_text: str,
    *,
    confirmed: bool,
    transport: EmailTransport,
    user_id: uuid.UUID,
) -> Envelope:
    """The one preview/send answer: identical merge, ``sent`` says which ran."""
    message = OutboundEmail(
        to_address=engagement.primary_contact_email or "",
        to_name=engagement.primary_contact_name or "",
        subject=subject,
        body=body_text,
        template_key=template_key,
    )
    if confirmed:
        transport.send(message)
        log.info(
            "templated email sent",
            extra={
                "context": {
                    "userId": str(user_id),
                    "engagementID": str(engagement.engagement_id),
                    "templateKey": template_key,
                }
            },
        )
    return ok(
        data={
            "templateKey": template_key,
            "to": {"address": message.to_address, "name": message.to_name},
            "subject": subject,
            "body": body_text,
            "sent": confirmed,
            "confirmation": (
                f"The email was sent to {message.to_address}." if confirmed else None
            ),
        }
    )


@router.get("/email/templates")
def list_email_templates(templates: _TemplatesDep, user_id: _UserDep) -> Envelope:
    """The staff-maintained template list the compose dialog offers (REQ-077)."""
    entries = templates.templates()
    return ok(
        data=[
            {
                "templateKey": template.template_key,
                "templateName": template.template_name,
                "mergeFields": sorted(template.merge_fields),
            }
            for template in entries
        ],
        meta={"totalCount": len(entries)},
    )


class EmailSendBody(BaseModel):
    """POST body: which template, for which engagement, preview or send."""

    model_config = ConfigDict(extra="forbid")

    template_key: str = Field(alias="templateKey", min_length=1)
    engagement_id: uuid.UUID = Field(alias="engagementID")
    # False = the preview round trip (REQ-077's review-before-send); the
    # client re-posts with True after the mentor has read the merged message.
    confirmed: bool = False


@router.post("/email/send")
def post_email_send(
    body: EmailSendBody,
    session: _SessionDep,
    user_id: _UserDep,
    roles: _RolesDep,
    templates: _TemplatesDep,
    transport: _TransportDep,
) -> Envelope:
    """Compose from a template with engagement merge fields (REQ-076/077).

    ``confirmed`` false answers the merged preview and sends NOTHING;
    ``confirmed`` true merges identically and hands the message to the
    transport seam. 422 on an unknown template, unfillable merge fields, or
    an engagement without a contact email; the engagement resolves through
    the same scoped 404 as every engagement verb.
    """
    engagement = _scoped_engagement(session, body.engagement_id, user_id, roles)
    if not engagement.primary_contact_email:
        raise _no_contact_refusal(engagement)
    context = _engagement_merge_context(session, engagement, user_id)
    subject, body_text = _merged_or_refused(body.template_key, templates, context)
    return _send_or_preview(
        engagement,
        body.template_key,
        subject,
        body_text,
        confirmed=body.confirmed,
        transport=transport,
        user_id=user_id,
    )


class ResourceShareBody(BaseModel):
    """POST body: which engagement receives the resource, preview or send."""

    model_config = ConfigDict(extra="forbid")

    engagement_id: uuid.UUID = Field(alias="engagementID")
    confirmed: bool = False


# The share flow's fixed template (REQ-084): sharing IS the resourceShare
# template carrying the resource link — one flow, one template, by design.
RESOURCE_SHARE_TEMPLATE_KEY: Final = "resourceShare"


@router.post("/resources/{resource_id}/share")
def post_resource_share(
    resource_id: uuid.UUID,
    body: ResourceShareBody,
    session: _SessionDep,
    user_id: _UserDep,
    roles: _RolesDep,
    templates: _TemplatesDep,
    transport: _TransportDep,
) -> Envelope:
    """Share one library resource with an engagement's contact (REQ-084).

    The templated-email flow with the resource's title and location joined
    into the merge context — the email carries the resource LINK; the
    library holds references, not attachments. Same preview-before-send
    contract as ``/email/send``.
    """
    resource = session.get(Resource, resource_id)
    if resource is None or resource.deleted_at is not None:
        raise RecordNotFoundError(_RESOURCE_ENTITY, str(resource_id))
    engagement = _scoped_engagement(session, body.engagement_id, user_id, roles)
    if not engagement.primary_contact_email:
        raise _no_contact_refusal(engagement)
    context = {
        **_engagement_merge_context(session, engagement, user_id),
        "resourceTitle": resource.resource_title,
        "resourceLocation": resource.resource_location,
    }
    subject, body_text = _merged_or_refused(RESOURCE_SHARE_TEMPLATE_KEY, templates, context)
    return _send_or_preview(
        engagement,
        RESOURCE_SHARE_TEMPLATE_KEY,
        subject,
        body_text,
        confirmed=body.confirmed,
        transport=transport,
        user_id=user_id,
    )
