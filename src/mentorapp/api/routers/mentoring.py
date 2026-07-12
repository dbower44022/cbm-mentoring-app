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
  Scheduling also runs the REQ-078/080 automation (WTK-170): the org-hosted
  meeting through the conferencing seam, the client's invite through the one
  email seam, and the ``transcriptRetrieval`` job for after the call.
- ``POST /sessions/{id}/transcript`` — the REQ-083 retrieve-now path
  (WTK-180/181): the platform transcript attaches append-only and the AI
  drafts land as PROPOSALS the mentor reviews; the PATCH's
  ``transcriptText`` is the paste path when automation cannot reach the
  meeting.
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
from mentorapp.automation.conferencing import (
    ConferencingProvider,
    FakeConferencingProvider,
    MeetingContext,
)
from mentorapp.automation.contact_detail import (
    ContactDetail,
    ContactDetailSource,
    DeterministicContactDetailSource,
)
from mentorapp.automation.email_outbound import (
    CatalogTemplateSource,
    EmailTemplateSource,
    EmailTransport,
    LoggedEmailTransport,
    MergeFieldError,
    OutboundEmail,
    merge_template,
)
from mentorapp.automation.transcripts import (
    TRANSCRIPT_RETRIEVAL_DELAY,
    TRANSCRIPT_RETRIEVAL_JOB_TYPE,
    DraftProposal,
    FakeSummaryDrafter,
    FakeTranscriptSource,
    RetrievedTranscript,
    SummaryDrafter,
    TranscriptSource,
    extended_transcript,
)
from mentorapp.automation.worker import JobHandler, PermanentJobError, enqueue_job
from mentorapp.observability import get_logger
from mentorapp.storage import (
    ENGAGEMENT_STATUS_OPTION_SET,
    SESSION_STATUS_OPTION_SET,
    AppUser,
    BackgroundJob,
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
CODE_TRANSCRIPT_APPEND_ONLY = "transcriptAppendOnly"
CODE_NO_APP_CREATED_MEETING = "noAppCreatedMeeting"
CODE_TRANSCRIPT_NOT_READY = "transcriptNotReady"

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
        # WTK-172 residual: pass 2 shipped the transitions INTO the paused
        # states with no way back — On Hold and Dormant were terminal by
        # accident. Resume is the deliberate way out, mentor-owned because
        # the mentor is who learns the client is back.
        LifecycleTransition(
            transition_key="resume",
            label="Resume Engagement",
            from_statuses=("onHold", "dormant"),
            to_status="active",
            confirmation=(
                "'{engagementName}' is Active again — sessions and follow-ups "
                "resume. Schedule the next session to get momentum back."
            ),
            refusal_what_next=(
                "Resume applies to a paused engagement (On Hold or Dormant). "
                "Pick one of those, or use the action this status calls for."
            ),
        ),
    )
}

# WTK-172 design notes — the two lifecycle gaps DELIBERATELY not built:
#
# - Assigned → Active has no mentor transition: an engagement becomes Active
#   when the work actually starts, and staff own that promotion today (it is
#   a staff data operation over the same status field). Auto-promoting on
#   the first held session is the candidate automation; it stays a design
#   note because REQ-075's confirmed vocabulary assigns no such rule and
#   inferences require positive support (conduct charter §11.6.b).
# - Dormant is NOT auto-flagged: a sweep could mark engagements dormant
#   after N quiet days, but dormancy is a judgment about the CLIENT going
#   quiet, not about row timestamps — and the triage order (REQ-072) already
#   surfaces quiet engagements by construction. If stakeholders later
#   confirm a threshold, the sweep is one background-job handler over the
#   triage read plus this module's existing "dormant" transition.


# --- Provider seams -------------------------------------------------------------------

# Module-level defaults are the sanctioned dev state (see email_outbound):
# templates come from the source-controlled catalog, sends land in the logged
# transport, meetings/transcripts/drafts come from the deterministic fakes.
# Deployment wiring overrides these dependencies to bind a stored template
# entity, a real SMTP/provider, the org-hosted Zoom (or Google Workspace)
# conferencing + transcript platform, and the chosen drafting model — without
# touching any endpoint, and with NO real external call ever made from here.
_TEMPLATE_SOURCE = CatalogTemplateSource()
_DEV_TRANSPORT = LoggedEmailTransport()
_DEV_CONFERENCING = FakeConferencingProvider()
_DEV_TRANSCRIPTS = FakeTranscriptSource()
_DEV_DRAFTER = FakeSummaryDrafter()
_DEV_CONTACT_DETAIL = DeterministicContactDetailSource()


def get_email_templates() -> EmailTemplateSource:
    """Provide the staff-maintained template list (REQ-077)."""
    return _TEMPLATE_SOURCE


def get_email_transport() -> EmailTransport:
    """Provide the outbound delivery seam (REQ-076) — dev default logs, never sends."""
    return _DEV_TRANSPORT


def get_conferencing_provider() -> ConferencingProvider:
    """Provide the org-meeting seam (REQ-080) — dev default books deterministic fakes."""
    return _DEV_CONFERENCING


def get_transcript_source() -> TranscriptSource:
    """Provide the transcript retrieval seam (REQ-083) — dev default is deterministic."""
    return _DEV_TRANSCRIPTS


def get_summary_drafter() -> SummaryDrafter:
    """Provide the AI drafting seam (REQ-083) — dev default extracts, never calls a model."""
    return _DEV_DRAFTER


def get_contact_detail_source() -> ContactDetailSource:
    """Provide the attendee contact-detail seam (REQ-110) — dev default is deterministic."""
    return _DEV_CONTACT_DETAIL


_SessionDep = Annotated[Session, Depends(get_session)]
_UserDep = Annotated[uuid.UUID, Depends(get_current_user_id)]
_RolesDep = Annotated[RoleSource, Depends(get_role_source)]
_TemplatesDep = Annotated[EmailTemplateSource, Depends(get_email_templates)]
_TransportDep = Annotated[EmailTransport, Depends(get_email_transport)]
_ConferencingDep = Annotated[ConferencingProvider, Depends(get_conferencing_provider)]
_TranscriptsDep = Annotated[TranscriptSource, Depends(get_transcript_source)]
_DrafterDep = Annotated[SummaryDrafter, Depends(get_summary_drafter)]
_ContactDetailDep = Annotated[ContactDetailSource, Depends(get_contact_detail_source)]


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
        # The REQ-080/083 automation facts: whether an app-created meeting
        # exists (drives the retrieve affordance), where the transcript came
        # from, and the standing draft proposals the mentor reviews.
        "externalMeetingID": record.external_meeting_id,
        "transcriptSource": record.transcript_source,
        "draftSummary": record.draft_summary,
        "draftActionItems": record.draft_action_items,
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


# --- The session details read (REQ-110, PI-015) -----------------------------------------


_TRANSCRIPT_ATTACHED: Final = "attached"
_TRANSCRIPT_EXPECTED: Final = "expected"
_TRANSCRIPT_UNAVAILABLE: Final = "unavailable"


def _scoped_session(
    session: Session, session_id: uuid.UUID, user_id: uuid.UUID, roles: RoleSource
) -> tuple[MentoringSession, Engagement]:
    """The caller's live session with its engagement, or the uniform 404.

    Sessions carry no access rule of their own — the engagement's pairing is
    the rule, so a session outside the caller's engagements answers exactly
    like one that never existed (the ``_scoped_engagement`` guarantee).
    """
    record = session.get(MentoringSession, session_id)
    if record is None or record.deleted_at is not None:
        raise RecordNotFoundError(_SESSION_ENTITY, str(session_id))
    engagement = _scoped_engagement(session, record.engagement_id, user_id, roles)
    return record, engagement


def _transcript_state(record: MentoringSession) -> str:
    """Which of the three surface states the transcript is in (REQ-110).

    ``expected`` is truthful because an app-created meeting always has the
    retrieval job standing (enqueued in the same transaction as the session);
    without one there is nothing to wait for — the paste path is the route.
    """
    if record.transcript_text:
        return _TRANSCRIPT_ATTACHED
    if record.external_meeting_id is not None:
        return _TRANSCRIPT_EXPECTED
    return _TRANSCRIPT_UNAVAILABLE


def _attendee_rows(
    session: Session,
    engagement: Engagement,
    record: MentoringSession,
    by_id: dict[uuid.UUID, OptionValue],
    details: ContactDetailSource,
) -> list[dict[str, Any]]:
    """The DERIVED attendee list (DEC-098): mentor + primary contact.

    Participation reads off the session status — ``invited`` until the
    session is marked completed, ``attended`` after — because per-person
    invited-vs-attended state is deliberately unmodeled pending its own
    ruling; this read never claims to know more than the session does.
    App-side values stay authoritative; the detail seam only fills gaps
    (phone, and whatever the production CRM binding adds later).
    """
    status = by_id.get(record.session_status) if record.session_status else None
    attended = status is not None and status.option_value_name == "completed"
    participation = "attended" if attended else "invited"

    mentor_ref = engagement.crm_mentor_ref
    crm_ids = [
        ref
        for ref in (
            mentor_ref.crm_mentor_id if mentor_ref is not None else None,
            engagement.primary_contact_crm_id,
        )
        if ref
    ]
    found = details.lookup(crm_ids) if crm_ids else {}

    client = (
        engagement.client
        if engagement.client and engagement.client.deleted_at is None
        else None
    )
    company = client.crm_company_ref if client is not None else None

    rows: list[dict[str, Any]] = []
    if mentor_ref is not None:
        detail = found.get(mentor_ref.crm_mentor_id, ContactDetail())
        mentor_user = session.get(AppUser, mentor_ref.user_id) if mentor_ref.user_id else None
        rows.append(
            {
                "name": detail.contact_name
                or (mentor_user.username if mentor_user else "Mentor"),
                "role": "mentor",
                "companyName": detail.company_name,
                "companyRefID": None,
                "crmContactID": mentor_ref.crm_mentor_id,
                "email": detail.email_address,
                "phone": detail.phone_number,
                "participation": participation,
            }
        )
    if engagement.primary_contact_name:
        crm_id = engagement.primary_contact_crm_id
        detail = found.get(crm_id, ContactDetail()) if crm_id else ContactDetail()
        rows.append(
            {
                "name": engagement.primary_contact_name,
                "role": "client",
                "companyName": detail.company_name
                or (company.crm_company_id if company else None),
                "companyRefID": company.crm_company_ref_id if company else None,
                "crmContactID": crm_id,
                "email": engagement.primary_contact_email or detail.email_address,
                "phone": detail.phone_number,
                "participation": participation,
            }
        )
    return rows


@router.get("/sessions/{session_id}/detail")
def get_session_detail(
    session_id: uuid.UUID,
    session: _SessionDep,
    user_id: _UserDep,
    roles: _RolesDep,
    details: _ContactDetailDep,
) -> Envelope:
    """Everything the session details surface renders, in one read (REQ-110).

    The transcript TEXT stays out of this payload by design — it is the
    record's longest content, so ``data.transcript`` carries only the state
    triple the surface's section renders from (attached / expected /
    unavailable, plus source and size), and the dedicated transcript read
    serves the text on demand. Attendees are the derived list documented on
    :func:`_attendee_rows`.
    """
    record, engagement = _scoped_session(session, session_id, user_id, roles)
    by_id, _ = _status_vocabulary(session, SESSION_STATUS_OPTION_SET)
    attendees = _attendee_rows(session, engagement, record, by_id, details)
    client = (
        engagement.client
        if engagement.client and engagement.client.deleted_at is None
        else None
    )
    company = client.crm_company_ref if client is not None else None
    word_count = len(record.transcript_text.split()) if record.transcript_text else 0
    log.info(
        "session detail served",
        extra={
            "context": {
                "userId": str(user_id),
                "sessionID": str(session_id),
                "attendeeCount": len(attendees),
                "transcriptState": _transcript_state(record),
            }
        },
    )
    return ok(
        data={
            "session": _session_entry(record, by_id),
            "engagement": {
                "engagementID": engagement.engagement_id,
                "engagementName": engagement.engagement_name,
            },
            "client": (
                {
                    "clientID": client.client_id,
                    "crmCompanyRefID": company.crm_company_ref_id if company else None,
                    "crmCompanyID": company.crm_company_id if company else None,
                }
                if client is not None
                else None
            ),
            "attendees": attendees,
            "transcript": {
                "state": _transcript_state(record),
                "source": record.transcript_source,
                "wordCount": word_count,
            },
        },
        meta={"attendeeCount": len(attendees)},
    )


@router.get("/sessions/{session_id}/transcript")
def get_session_transcript(
    session_id: uuid.UUID,
    session: _SessionDep,
    user_id: _UserDep,
    roles: _RolesDep,
) -> Envelope:
    """The transcript text, on demand (REQ-110).

    Fetched only when the details surface's transcript section is actually
    used — an hour of conversation never rides along on a preview read. A
    session whose transcript is not attached answers its state with a null
    text, and the surface's own educate copy explains it; asking is not an
    error.
    """
    record, _ = _scoped_session(session, session_id, user_id, roles)
    state = _transcript_state(record)
    text = record.transcript_text if state == _TRANSCRIPT_ATTACHED else None
    log.info(
        "session transcript served",
        extra={
            "context": {
                "userId": str(user_id),
                "sessionID": str(session_id),
                "state": state,
                "wordCount": len(text.split()) if text else 0,
            }
        },
    )
    return ok(
        data={
            "state": state,
            "transcriptText": text,
            "transcriptSource": record.transcript_source,
        },
        meta={"wordCount": len(text.split()) if text else 0},
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


def _created_meeting_or_none(
    provider: ConferencingProvider, engagement: Engagement, scheduled_at: datetime
) -> tuple[str | None, str | None]:
    """Book the REQ-080 org meeting: ``(joinUrl, externalMeetingID)`` or ``(None, None)``.

    A provider failure must never fail the SCHEDULING (the REQ-079 paste path
    is the universal fallback), so it degrades to no-meeting with a logged
    error — the invite skip reason then tells the mentor to paste a link.
    """
    try:
        created = provider.create_meeting(
            MeetingContext(
                engagement_id=engagement.engagement_id,
                engagement_name=engagement.engagement_name or "",
                contact_name=engagement.primary_contact_name or "",
                contact_email=engagement.primary_contact_email or "",
                scheduled_at=scheduled_at,
            )
        )
    except Exception:
        log.exception(
            "conference meeting creation failed; scheduling continues without a link",
            extra={"context": {"engagementID": str(engagement.engagement_id)}},
        )
        return None, None
    return created.join_url, created.external_meeting_id


def _send_session_invite(
    session: Session,
    engagement: Engagement,
    record: MentoringSession,
    *,
    templates: EmailTemplateSource,
    transport: EmailTransport,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """Send the REQ-078 meeting invite; returns the honest invite outcome.

    The invite rides the ONE email seam (Doug's 2026-07-06 ruling): the
    ``sessionInvite`` template merged with the session facts and handed to
    the transport. Every skip is reported, never silent — no contact email,
    no conference link (automated path unavailable and nothing pasted), or a
    template list that no longer carries the invite. A skip never unwinds
    the scheduling: the session exists either way.
    """
    if not engagement.primary_contact_email:
        return {
            "sent": False,
            "toAddress": None,
            "reason": (
                "No invite was sent: this engagement has no primary contact "
                "email on record. Ask the program office to complete it, then "
                "share the link from the session."
            ),
        }
    if not record.conference_link:
        return {
            "sent": False,
            "toAddress": None,
            "reason": (
                "No invite was sent: the session has no conference link — the "
                "automated meeting could not be created. Paste a meeting link "
                "onto the session and share it with the client."
            ),
        }
    template = templates.template(SESSION_INVITE_TEMPLATE_KEY)
    if template is None:
        # The catalog is staff-maintained data behind a seam; a deployment
        # that removed the invite template is a configuration defect to log
        # loudly, not a reason to refuse the scheduling.
        log.error(
            "session invite template missing from the template source",
            extra={"context": {"templateKey": SESSION_INVITE_TEMPLATE_KEY}},
        )
        return {
            "sent": False,
            "toAddress": None,
            "reason": (
                "No invite was sent: the session invitation template is "
                "missing from the staff template list. Tell an administrator; "
                "meanwhile share the conference link from the session."
            ),
        }
    context = {
        **_engagement_merge_context(session, engagement, user_id),
        # UTC, stated explicitly: the store is UTC and the invite must never
        # imply a local zone it cannot know — client-side presentation of
        # times is the app's concern, an email states its zone.
        "sessionTime": f"{as_utc(record.scheduled_at):%Y-%m-%d %H:%M} UTC",
        "conferenceLink": record.conference_link,
    }
    try:
        subject, body_text = merge_template(template, context)
    except MergeFieldError as missing:
        # A carried-over engagement can lack its name/contact fields; a
        # half-merged invite must never send (the merge module's one rule),
        # and an unfillable invite must never unwind the scheduling.
        return {
            "sent": False,
            "toAddress": None,
            "reason": (
                "No invite was sent: the engagement is missing "
                f"{', '.join(sorted(missing.missing_fields))}, which the "
                "invitation needs. Staff completes engagement details; share "
                "the conference link from the session meanwhile."
            ),
        }
    transport.send(
        OutboundEmail(
            to_address=engagement.primary_contact_email,
            to_name=engagement.primary_contact_name or "",
            subject=subject,
            body=body_text,
            template_key=SESSION_INVITE_TEMPLATE_KEY,
        )
    )
    log.info(
        "session invite sent",
        extra={
            "context": {
                "userId": str(user_id),
                "sessionID": str(record.session_id),
                "toAddress": engagement.primary_contact_email,
            }
        },
    )
    return {"sent": True, "toAddress": engagement.primary_contact_email, "reason": None}


# The scheduling flow's fixed template (the resourceShare precedent): the
# invite is sent BY the flow, not composed by the mentor.
SESSION_INVITE_TEMPLATE_KEY: Final = "sessionInvite"


@router.post("/engagements/{engagement_id}/sessions")
def post_session_create(
    engagement_id: uuid.UUID,
    body: SessionCreateBody,
    session: _SessionDep,
    user_id: _UserDep,
    roles: _RolesDep,
    templates: _TemplatesDep,
    transport: _TransportDep,
    conferencing: _ConferencingDep,
) -> Envelope:
    """Schedule one session: org meeting, invite, transcript automation (REQ-078/080).

    Created ``scheduled``. A pasted ``conferenceLink`` (REQ-079 — the
    universal fallback) is taken verbatim and books nothing; otherwise the
    conferencing seam creates the org-hosted meeting and the session carries
    its link plus ``externalMeetingID``. Scheduling then sends the client the
    meeting invite through the one email seam (``meta.invite`` reports sent
    or the skip reason — never silent), and an app-created meeting enqueues
    the ``transcriptRetrieval`` job to run after the session (REQ-083).
    Rides :func:`create_record` so registry validation, audit stamping, and
    the feed apply exactly as everywhere.
    """
    engagement = _scoped_engagement(session, engagement_id, user_id, roles)
    _, by_name = _status_vocabulary(session, SESSION_STATUS_OPTION_SET)
    conference_link, external_meeting_id = (
        (body.conference_link, None)
        if body.conference_link is not None
        else _created_meeting_or_none(conferencing, engagement, body.scheduled_at)
    )
    record = create_record(
        session,
        MentoringSession,
        _SESSION_ENTITY,
        {
            "engagementID": engagement.engagement_id,
            "scheduledAt": body.scheduled_at,
            "sessionStatus": by_name["scheduled"].option_value_id,
            "conferenceLink": conference_link,
            "externalMeetingID": external_meeting_id,
        },
        acting_user_id=user_id,
    )
    invite = _send_session_invite(
        session, engagement, record, templates=templates, transport=transport, user_id=user_id
    )
    if external_meeting_id is not None:
        # The automated half of REQ-083: retrieval waits until the session
        # has plausibly ended; "not produced yet" retries on the worker's
        # backoff. Same transaction as the session row — a scheduled session
        # is never missing its retrieval job.
        enqueue_job(
            session,
            TRANSCRIPT_RETRIEVAL_JOB_TYPE,
            {"sessionID": str(record.session_id)},
            run_after=as_utc(body.scheduled_at) + TRANSCRIPT_RETRIEVAL_DELAY,
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
                "meetingCreated": external_meeting_id is not None,
                "inviteSent": invite["sent"],
            }
        },
    )
    return ok(
        data=serialize_record(record),
        meta={
            "affectedDataSourceKeys": [DS_MENTOR_SESSIONS, *_ENGAGEMENT_SOURCE_KEYS],
            "invite": invite,
        },
    )


class SessionPatchBody(BaseModel):
    """PATCH body: the entry fields the prep surface writes (REQ-082/083).

    ``sessionNotes``/``actionItems`` carry the rich-text control's clean
    HTML; ``sessionStatus`` travels as the option-value NAME (the stable
    identifier) and is resolved to its ``optionValueID`` here.
    ``transcriptText``/``transcriptSource`` are the REQ-083 PASTE path
    (append-only — the automation's rule and the mentor's are the same);
    ``draftSummary``/``draftActionItems`` let the client clear or amend a
    proposal once the mentor has accepted or dismissed it.
    """

    model_config = ConfigDict(extra="forbid")

    row_version: int = Field(alias="rowVersion")
    session_notes: str | None = Field(default=None, alias="sessionNotes", max_length=4000)
    action_items: str | None = Field(default=None, alias="actionItems", max_length=4000)
    conference_link: str | None = Field(default=None, alias="conferenceLink", max_length=2000)
    session_status: str | None = Field(default=None, alias="sessionStatus")
    transcript_text: str | None = Field(default=None, alias="transcriptText")
    transcript_source: str | None = Field(
        default=None, alias="transcriptSource", max_length=200
    )
    draft_summary: str | None = Field(default=None, alias="draftSummary", max_length=4000)
    draft_action_items: str | None = Field(
        default=None, alias="draftActionItems", max_length=4000
    )


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
    record, _ = _scoped_session(session, session_id, user_id, roles)

    sent = body.model_fields_set
    changes: dict[str, Any] = {}
    for attr, field_name in (
        ("session_notes", "sessionNotes"),
        ("action_items", "actionItems"),
        ("conference_link", "conferenceLink"),
        ("transcript_source", "transcriptSource"),
        ("draft_summary", "draftSummary"),
        ("draft_action_items", "draftActionItems"),
    ):
        if attr in sent:
            changes[field_name] = getattr(body, attr)
    if "transcript_text" in sent:
        # The WTK-182 rule at the API boundary, refused in words rather than
        # left to the model guard's exception: a transcript is evidence — it
        # may be EXTENDED (the new value starts with the stored one), never
        # rewritten or cleared. The paste path therefore appends.
        current = record.transcript_text
        wanted = body.transcript_text
        if current is not None and (wanted is None or not wanted.startswith(current)):
            raise ApiValidationError(
                [
                    field_error(
                        "transcriptText",
                        CODE_TRANSCRIPT_APPEND_ONLY,
                        "The transcript was not changed. A session's "
                        "transcript is append-only evidence — paste ADDS to "
                        "what is stored, never rewrites or clears it. Include "
                        "the existing transcript at the start, or paste only "
                        "onto a session without one.",
                    )
                ]
            )
        changes["transcriptText"] = wanted
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


# --- Transcript retrieval + draft proposals (WTK-180/181, REQ-083) ----------------------


def attach_transcript_and_draft(
    db_session: Session,
    record: MentoringSession,
    retrieved: RetrievedTranscript,
    drafter: SummaryDrafter,
    *,
    acting_user_id: uuid.UUID | None = None,
) -> DraftProposal:
    """Attach one retrieved transcript and stage its proposals (REQ-083).

    Lives in the API layer (not :mod:`mentorapp.automation.transcripts`)
    because it rides :func:`partial_update` — the one write engine — which
    the automation layer sits below. Applied against the record's CURRENT
    ``rowVersion``: this is a system write layering new material on, not an
    edit contest with the mentor, and it touches ONLY the transcript and
    draft columns. A newer draft REPLACES the previous proposal (a proposal
    is machine output, not authored content); ``sessionNotes`` and
    ``actionItems`` are never written here — the mentor stays the author of
    record. Flushes, does not commit — the caller owns the transaction.
    """
    drafts = drafter.draft(retrieved.transcript_text)
    changes: dict[str, Any] = {
        "transcriptSource": retrieved.transcript_source,
        "draftSummary": drafts.draft_summary,
        "draftActionItems": drafts.draft_action_items,
    }
    merged = extended_transcript(record.transcript_text, retrieved.transcript_text)
    if merged is not None:
        changes["transcriptText"] = merged
    partial_update(
        db_session,
        record,
        _SESSION_ENTITY,
        changes,
        row_version=record.row_version,
        acting_user_id=acting_user_id,
    )
    log.info(
        "transcript attached with draft proposals",
        extra={
            "context": {
                "sessionID": str(record.session_id),
                "transcriptSource": retrieved.transcript_source,
                "transcriptExtended": merged is not None,
            }
        },
    )
    return drafts


def transcript_retrieval_job(source: TranscriptSource, drafter: SummaryDrafter) -> JobHandler:
    """The queue handler for ``transcriptRetrieval`` jobs (WTK-181, REQ-083).

    Payload contract (wire names): ``sessionID`` — the session whose
    app-created meeting to retrieve. A missing/deleted session or one with
    no ``externalMeetingID`` parks permanently (retrying cannot conjure a
    meeting); a transcript the platform has not produced yet raises a plain
    error so the worker retries on its backoff — the crash-safe version of
    "try again later". Worker wiring closes over the deployment's source and
    drafter, exactly the artifact-store handler-factory shape.
    """

    def handle(db_session: Session, job: BackgroundJob) -> None:
        raw_id = job.job_payload.get("sessionID")
        try:
            target_id = uuid.UUID(str(raw_id))
        except ValueError as exc:
            raise PermanentJobError(
                f"transcript payload names no valid session: {raw_id!r}"
            ) from exc
        record = db_session.get(MentoringSession, target_id)
        if record is None or record.deleted_at is not None:
            raise PermanentJobError(f"session {target_id} no longer exists")
        if record.external_meeting_id is None:
            raise PermanentJobError(
                f"session {target_id} has no app-created meeting; the paste "
                "path is the only transcript route for it"
            )
        retrieved = source.retrieve(record.external_meeting_id)
        if retrieved is None:
            # Transient by definition: the platform simply hasn't produced it
            # yet — the worker retries with backoff and parks after the cap.
            raise RuntimeError(
                f"transcript for meeting {record.external_meeting_id} not ready yet"
            )
        attach_transcript_and_draft(
            db_session, record, retrieved, drafter, acting_user_id=job.created_by
        )
        return None

    return handle


@router.post("/sessions/{session_id}/transcript")
def post_session_transcript(
    session_id: uuid.UUID,
    session: _SessionDep,
    user_id: _UserDep,
    roles: _RolesDep,
    transcripts: _TranscriptsDep,
    drafter: _DrafterDep,
) -> Envelope:
    """Retrieve the meeting's AI transcript NOW and stage the drafts (REQ-083).

    The mentor-triggered twin of the ``transcriptRetrieval`` job — same
    seams, same attach rule. Scoped through the session's engagement (the
    uniform 404). 422 ``noAppCreatedMeeting`` when the session's link was
    pasted (nothing to ask the platform for — the paste path is the route);
    422 ``transcriptNotReady`` while the platform has not produced it.
    Success answers the updated session record; the drafts land as PROPOSALS
    on ``draftSummary``/``draftActionItems`` — never on the mentor's notes.
    """
    record, _ = _scoped_session(session, session_id, user_id, roles)
    if record.external_meeting_id is None:
        raise ApiValidationError(
            [
                field_error(
                    "sessionID",
                    CODE_NO_APP_CREATED_MEETING,
                    "No transcript was retrieved. This session's conference "
                    "link was pasted, so no app-created org meeting exists to "
                    "ask the platform about. Paste the transcript into the "
                    "session instead — the drafts work the same way.",
                )
            ]
        )
    retrieved = transcripts.retrieve(record.external_meeting_id)
    if retrieved is None:
        raise ApiValidationError(
            [
                field_error(
                    "sessionID",
                    CODE_TRANSCRIPT_NOT_READY,
                    "No transcript was retrieved. The conference platform "
                    "hasn't produced this meeting's transcript yet — it "
                    "usually appears shortly after the call ends. Try again "
                    "in a few minutes, or paste the transcript if the "
                    "platform never produces one.",
                )
            ]
        )
    attach_transcript_and_draft(session, record, retrieved, drafter, acting_user_id=user_id)
    session.commit()
    log.info(
        "transcript retrieved on demand",
        extra={"context": {"userId": str(user_id), "sessionID": str(session_id)}},
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
                    f"resource's Share action; the session invitation sends "
                    f"itself when a session is scheduled; engagement "
                    f"templates need the engagement's contact and name "
                    f"completed.",
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
