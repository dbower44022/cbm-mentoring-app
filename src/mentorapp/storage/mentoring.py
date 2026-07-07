"""Application-owned mentoring domain entities (PI-010, WTK-164/165/166/173/174/175/182).

The PI-010 domain data foundation, reconciled onto what PI-009 delivered
(one canonical home per concept — SKL-122, never a parallel entity):

- **Company subclassing (REQ-086).** ONE organization record: the CRM-side
  anchor :class:`~mentorapp.storage.crm_refs.CrmCompanyRef` (the renamed
  ``crmClientRef``). The *client* and *partner* roles a company plays are the
  app-owned subclass tables here (:class:`Client` / :class:`Partner`), each
  1:1 on the company anchor and carrying only role-specific working fields —
  a company that is both client and partner has one anchor row plus one row
  per role, never a duplicate company row.
- **Engagement (REQ-075/REQ-072/REQ-074).** The former ``crmEngagementRef``
  anchor was EXTENDED into :class:`Engagement` (renamed by migration 0014):
  PI-010's stakeholder-confirmed requirements put the engagement's working
  data app-side — its status vocabulary is option-set DATA (REQ-075, a DB-S7
  choice field, which only exists in this store), and the REQ-072 triage
  columns must be derivable server-side from the app read surface. The
  ownership side therefore flips to "application"; ``crmEngagementID``
  survives as the now-optional anchor to a CRM engagement record where one
  exists.
- **Session (REQ-074/REQ-079/REQ-082, WTK-182).** The former ``sessionLog``
  was EXTENDED into :class:`MentoringSession` (table ``session``): notes and
  action items are entered ON the session as rich-text fields (REQ-074), and
  action items are deliberately a rich-text bulleted field, NOT structured
  task records (REQ-082) — which is why 0014 also retires the PI-009
  ``meetingNote`` and ``nextStep`` tables: their concepts now live on the
  session's ``sessionNotes``/``actionItems`` fields, one home each.
- **Resource (REQ-084) / Event (REQ-085).** Staff-maintained library entries
  and staff-defined events. Mentors read both through granted data sources;
  writes are gated by the ``resource.manage``/``event.manage`` capabilities
  (:mod:`mentorapp.access.mentoring`) — storage carries no permission.

:class:`ProgressGoal` continues unchanged from PI-009 (REQ-063): goals are
not superseded by any PI-010 requirement.

Everything lives under the platform data standards — entity-named UUIDv7
keys, the structural system columns, partial live-row indexes, DB-R2b
foreign keys carrying the exact name of the key they reference — and every
choice vocabulary is option-set data seeded from the column-site
declarations (DB-S7), never a database enum.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Final

from sqlalchemy import Date, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from mentorapp.storage.crm_refs import CrmCompanyRef, CrmMentorRef
from mentorapp.storage.entity import BaseEntity, entity_key, entity_ref, live_index, live_unique

# Free-text mentoring narrative (session notes, summaries) versus short
# descriptive lines (a goal, a program name). Widths, not contracts.
_NARRATIVE_LENGTH = 4000
_DESCRIPTION_LENGTH = 2000
_LINE_LENGTH = 200
# 320 covers the maximal RFC email shape (the actionToken precedent).
_EMAIL_LENGTH = 320
# CRM record ids — same headroom as the crm_refs anchors.
_CRM_ID_LENGTH = 64
_URL_LENGTH = 2000

# REQ-090: narrative columns carry clean semantic HTML from the one rich-text
# control — the registry fieldType routes them to
# ``ui.entry_editors.RICH_TEXT_CONTROL``, never a UI-side field-name list.
_RICH_TEXT_REGISTRY = {"fieldType": "richText", "searchableFlag": True}

# REQ-075's stakeholder-confirmed engagement status vocabulary, seeded as
# option-set DATA (DB-S7) from this one declaration. Relabeling or retiring a
# value is an admin data operation; records store the optionValueID.
ENGAGEMENT_STATUS_OPTION_SET: Final = "engagementStatus"
ENGAGEMENT_STATUS_VALUES: Final[tuple[tuple[str, str], ...]] = (
    ("active", "Active"),
    ("pendingAcceptance", "Pending Acceptance"),
    ("assigned", "Assigned"),
    ("onHold", "On Hold"),
    ("dormant", "Dormant"),
    ("assignmentDeclined", "Assignment Declined"),
)

# A session is scheduled, then completed. Cancellation is deliberately NOT a
# status: a cancelled session is soft-deleted (the row survives as evidence,
# per the retain-never-delete rule), which is also what keeps the REQ-072
# triage aggregates truthful over live rows without decoding status IDs in
# SQL.
SESSION_STATUS_OPTION_SET: Final = "sessionStatus"
SESSION_STATUS_VALUES: Final[tuple[tuple[str, str], ...]] = (
    ("scheduled", "Scheduled"),
    ("completed", "Completed"),
)

# REQ-084 names the library's three kinds: documents, videos, links.
RESOURCE_KIND_OPTION_SET: Final = "resourceKind"
RESOURCE_KIND_VALUES: Final[tuple[tuple[str, str], ...]] = (
    ("document", "Document"),
    ("video", "Video"),
    ("link", "Link"),
)


class Client(BaseEntity):
    """The *client* role of one company — a REQ-086 subclass row.

    Role-specific working fields only: the organization's master data stays
    in the CRM behind the company anchor. At most one live client role per
    company (partial unique on the anchor key) — the role either exists or
    it does not; its history survives as soft-deleted rows. The role fields
    are free text until a stakeholder-confirmed vocabulary exists; promoting
    one to a choice field is an option-set data operation plus a column
    retype, not a redesign.
    """

    __tablename__ = "client"
    __ownership_side__ = "application"
    __table_args__ = (live_unique("uq_client_crmCompanyRefID_live", "crmCompanyRefID"),)

    client_id: Mapped[uuid.UUID] = entity_key("clientID")
    # Re-pointing the role at a different company changes what every
    # engagement under it is about — history-track the association (DB-S5).
    crm_company_ref_id: Mapped[uuid.UUID] = entity_ref(
        "crmCompanyRef.crmCompanyRefID",
        registry={"fieldLabel": "CRM Company Ref ID", "historyTrackedFlag": True},
    )
    client_since: Mapped[date | None] = mapped_column("clientSince", Date())
    client_program: Mapped[str | None] = mapped_column("clientProgram", String(_LINE_LENGTH))
    client_referral_source: Mapped[str | None] = mapped_column(
        "clientReferralSource", String(_LINE_LENGTH)
    )
    client_stage: Mapped[str | None] = mapped_column("clientStage", String(_LINE_LENGTH))

    crm_company_ref: Mapped[CrmCompanyRef] = relationship()


class Partner(BaseEntity):
    """The *partner* role of one company — the minimal REQ-086 subclass row.

    Deliberately no role fields yet: the live row IS the role declaration
    (REQ-086 asks for the subclass model, not invented partner attributes),
    and the same partial 1:1 unique as :class:`Client` guarantees one live
    partner role per company. Role fields land here when stakeholders
    confirm them — a column addition, never a second company row.
    """

    __tablename__ = "partner"
    __ownership_side__ = "application"
    __table_args__ = (live_unique("uq_partner_crmCompanyRefID_live", "crmCompanyRefID"),)

    partner_id: Mapped[uuid.UUID] = entity_key("partnerID")
    crm_company_ref_id: Mapped[uuid.UUID] = entity_ref(
        "crmCompanyRef.crmCompanyRefID",
        registry={"fieldLabel": "CRM Company Ref ID", "historyTrackedFlag": True},
    )

    crm_company_ref: Mapped[CrmCompanyRef] = relationship()


class Engagement(BaseEntity):
    """One mentoring engagement — the app-owned working home (REQ-075/REQ-072).

    Extended from PI-009's ``crmEngagementRef`` anchor (migration 0014):
    the status vocabulary is REQ-075 option-set data and the REQ-072 triage
    columns must be derivable server-side, so the working data lives here;
    ``crmEngagementID`` remains as the optional anchor to a CRM engagement
    record where one exists.

    Nullability vs. requiredness: ``engagementName``, ``engagementStatus``
    and ``clientID`` are API-required (registry ``requiredFlag``) but
    database-nullable, because the 0014 rename carries pre-PI-010 anchor
    rows that had none of these — staff complete them; the API refuses new
    records without them. ``crmMentorRefID`` is genuinely optional: the
    REQ-075 vocabulary (Pending Acceptance, Assignment Declined) means an
    engagement exists before — and after a declined — mentor assignment.

    The primary contact is working data the triage read (REQ-072) must
    serve from the app store: ``primaryContactName``/``primaryContactEmail``
    are the engagement's designated contact as staff maintain it here, and
    ``primaryContactCrmID`` optionally anchors the CRM contact record the
    person also is. Contact master data browsing stays a CRM read — these
    columns exist because the triage columns are server-side app truth, not
    to fork the CRM's contact book.
    """

    __tablename__ = "engagement"
    __ownership_side__ = "application"
    __table_args__ = (
        # One live engagement row per CRM engagement record, exactly as the
        # anchor enforced; NULL anchors (app-born engagements) never collide.
        live_unique("uq_engagement_crmEngagementID_live", "crmEngagementID"),
        # The client's engagement list and the mentor's engagement list —
        # the two scans every REQ-071 area read leads with.
        live_index("ix_engagement_clientID_live", "clientID"),
        live_index("ix_engagement_crmMentorRefID_live", "crmMentorRefID"),
    )

    engagement_id: Mapped[uuid.UUID] = entity_key("engagementID")
    crm_engagement_id: Mapped[str | None] = mapped_column(
        "crmEngagementID",
        String(_CRM_ID_LENGTH),
        info={"registry": {"fieldLabel": "CRM Engagement ID", "historyTrackedFlag": True}},
    )
    engagement_name: Mapped[str | None] = mapped_column(
        "engagementName",
        String(_LINE_LENGTH),
        info={"registry": {"requiredFlag": True, "searchableFlag": True}},
    )
    # DB-S7 choice column: stores the optionValueID; the REQ-075 vocabulary
    # is seeded from the declaration and status flips are history-tracked.
    engagement_status: Mapped[uuid.UUID | None] = mapped_column(
        "engagementStatus",
        info={
            "registry": {
                "requiredFlag": True,
                "historyTrackedFlag": True,
                "optionSet": ENGAGEMENT_STATUS_OPTION_SET,
                "optionValues": ENGAGEMENT_STATUS_VALUES,
            }
        },
    )
    client_id: Mapped[uuid.UUID | None] = entity_ref(
        "client.clientID",
        nullable=True,
        registry={"requiredFlag": True, "historyTrackedFlag": True},
    )
    crm_mentor_ref_id: Mapped[uuid.UUID | None] = entity_ref(
        "crmMentorRef.crmMentorRefID",
        nullable=True,
        registry={"fieldLabel": "CRM Mentor Ref ID", "historyTrackedFlag": True},
    )
    engagement_summary: Mapped[str | None] = mapped_column(
        "engagementSummary",
        String(_NARRATIVE_LENGTH),
        info={"registry": _RICH_TEXT_REGISTRY},
    )
    primary_contact_name: Mapped[str | None] = mapped_column(
        "primaryContactName",
        String(_LINE_LENGTH),
        info={"registry": {"searchableFlag": True}},
    )
    primary_contact_email: Mapped[str | None] = mapped_column(
        "primaryContactEmail",
        String(_EMAIL_LENGTH),
        info={"registry": {"searchableFlag": True}},
    )
    primary_contact_crm_id: Mapped[str | None] = mapped_column(
        "primaryContactCrmID",
        String(_CRM_ID_LENGTH),
        info={"registry": {"fieldLabel": "Primary Contact CRM ID"}},
    )

    client: Mapped[Client | None] = relationship()
    crm_mentor_ref: Mapped[CrmMentorRef | None] = relationship()
    # REQ-074's aggregation shape: sessions are entered against the
    # engagement; the engagement-side read walks this association.
    sessions: Mapped[list[MentoringSession]] = relationship(back_populates="engagement")


class MentoringSession(BaseEntity):
    """One mentoring session of one engagement (REQ-074/REQ-079/REQ-082, WTK-182).

    Extended from PI-009's ``sessionLog`` (migration 0014 renamed the table
    to ``session`` and its date/summary columns to ``scheduledAt``/
    ``sessionNotes``). Notes and action items are entered HERE (REQ-074) as
    rich-text fields; ``actionItems`` is the REQ-082 bulleted rich-text
    field — deliberately no structured task records exist. ``sessionNotes``
    became nullable with the rename: a session is now scheduled before it
    happens, so an empty-notes row is a future session, not a defect.

    Transcript retention (WTK-182): ``transcriptText`` is unbounded
    ``Text`` because captured transcripts are third-party artifacts whose
    size the app does not control, and ``transcriptSource`` records where a
    transcript came from (which tool/upload) so retention questions stay
    answerable. The transcript is append-only app-side — see
    :meth:`_append_only_transcript`.
    """

    __tablename__ = "session"
    __ownership_side__ = "application"
    __table_args__ = (
        # The REQ-072/REQ-074 read leads with the engagement and orders by
        # time (last/next session, the engagement's session history) — one
        # composite live index serves both, and its leading column serves
        # the plain "sessions of this engagement" scan.
        live_index("ix_session_engagement_scheduledAt_live", "engagementID", "scheduledAt"),
    )

    session_id: Mapped[uuid.UUID] = entity_key("sessionID")
    # Re-pointing a session at a different engagement changes what the whole
    # record is about — history-track the association (DB-S5).
    engagement_id: Mapped[uuid.UUID] = entity_ref(
        "engagement.engagementID",
        registry={"historyTrackedFlag": True},
    )
    scheduled_at: Mapped[datetime] = mapped_column("scheduledAt", nullable=False)
    session_status: Mapped[uuid.UUID | None] = mapped_column(
        "sessionStatus",
        info={
            "registry": {
                "requiredFlag": True,
                "historyTrackedFlag": True,
                "optionSet": SESSION_STATUS_OPTION_SET,
                "optionValues": SESSION_STATUS_VALUES,
            }
        },
    )
    # REQ-079: the session carries its conference link; width matches the
    # platform's URL columns (helpMapping.helpURL).
    conference_link: Mapped[str | None] = mapped_column("conferenceLink", String(_URL_LENGTH))
    session_notes: Mapped[str | None] = mapped_column(
        "sessionNotes",
        String(_NARRATIVE_LENGTH),
        info={"registry": _RICH_TEXT_REGISTRY},
    )
    action_items: Mapped[str | None] = mapped_column(
        "actionItems",
        String(_NARRATIVE_LENGTH),
        info={"registry": _RICH_TEXT_REGISTRY},
    )
    transcript_text: Mapped[str | None] = mapped_column("transcriptText", Text())
    transcript_source: Mapped[str | None] = mapped_column(
        "transcriptSource", String(_LINE_LENGTH)
    )

    engagement: Mapped[Engagement] = relationship(back_populates="sessions")

    @validates("transcript_text")
    def _append_only_transcript(self, _key: str, value: str | None) -> str | None:
        # WTK-182's retention discipline at the persistence boundary: a
        # captured transcript is evidence — a later capture may EXTEND it
        # (the new value starts with the old), but nothing app-side may
        # rewrite or clear it. Same backstop shape as the workprocess
        # vocabularies: writers that never ride the API cannot break it.
        # Read through the attribute, not __dict__: after a commit the value
        # is expired, and the guard must compare against the COMMITTED
        # transcript (the get refreshes it), never a blank.
        current = self.transcript_text
        if current is not None and (value is None or not value.startswith(current)):
            raise ValueError(
                "transcriptText is append-only: it may be extended, "
                "never rewritten or cleared (WTK-182)."
            )
        return value


class ProgressGoal(BaseEntity):
    """A goal progress is tracked against (REQ-063) — unchanged from PI-009."""

    __tablename__ = "progressGoal"
    __ownership_side__ = "application"

    progress_goal_id: Mapped[uuid.UUID] = entity_key("progressGoalID")
    progress_goal_description: Mapped[str] = mapped_column(
        "progressGoalDescription",
        String(_DESCRIPTION_LENGTH),
        nullable=False,
        info={"registry": _RICH_TEXT_REGISTRY},
    )


class Resource(BaseEntity):
    """One staff-maintained library entry: a document, video, or link (REQ-084).

    ``resourceLocation`` is where the thing lives (a URL or storage
    locator) — the library holds references, not blobs. Who may maintain
    the library is the ``resource.manage`` capability
    (:mod:`mentorapp.access.mentoring`); mentors reach it read-only through
    the granted resources data source.
    """

    __tablename__ = "resource"
    __ownership_side__ = "application"

    resource_id: Mapped[uuid.UUID] = entity_key("resourceID")
    resource_title: Mapped[str] = mapped_column(
        "resourceTitle",
        String(_LINE_LENGTH),
        nullable=False,
        info={"registry": {"searchableFlag": True}},
    )
    resource_kind: Mapped[uuid.UUID | None] = mapped_column(
        "resourceKind",
        info={
            "registry": {
                "requiredFlag": True,
                "optionSet": RESOURCE_KIND_OPTION_SET,
                "optionValues": RESOURCE_KIND_VALUES,
            }
        },
    )
    resource_location: Mapped[str] = mapped_column(
        "resourceLocation", String(_URL_LENGTH), nullable=False
    )
    resource_description: Mapped[str | None] = mapped_column(
        "resourceDescription", String(_DESCRIPTION_LENGTH)
    )


class Event(BaseEntity):
    """One staff-defined event mentors see read-only (REQ-085).

    Storage carries no permission: mentors read events through the granted
    events data source, and defining or editing one is the ``event.manage``
    capability (:mod:`mentorapp.access.mentoring`) they do not hold.
    """

    __tablename__ = "event"
    __ownership_side__ = "application"
    __table_args__ = (
        # The "what's coming up" read: live events in time order.
        live_index("ix_event_startsAt_live", "startsAt"),
    )

    event_id: Mapped[uuid.UUID] = entity_key("eventID")
    event_title: Mapped[str] = mapped_column(
        "eventTitle",
        String(_LINE_LENGTH),
        nullable=False,
        info={"registry": {"searchableFlag": True}},
    )
    starts_at: Mapped[datetime] = mapped_column("startsAt", nullable=False)
    event_location: Mapped[str | None] = mapped_column("eventLocation", String(_LINE_LENGTH))
    # Who the event is for, as staff describe it ("all mentors", a cohort) —
    # free text until an audience vocabulary is stakeholder-confirmed.
    event_audience: Mapped[str | None] = mapped_column("eventAudience", String(_LINE_LENGTH))
