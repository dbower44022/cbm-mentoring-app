"""Application-owned mentoring-process entities (REQ-063, WTK-156).

Mentoring-process data has no system of record today, so these data sets are
owned by the application store (``__ownership_side__ = "application"``, the
REQ-063 declaration ``BaseEntity`` enforces at class definition): session
logs, meeting notes, next steps, and progress against goals. They live under
the platform data standards — entity-named UUIDv7 keys, the structural
system columns, partial live-row indexes — and link to the CRM records they
concern through the REQ-062 anchors, never to raw CRM ids.

``sessionLog.crmEngagementRefID`` is the many-to-one association to
``crmEngagementRef``: a logged session persists app-side linked to its
engagement's CRM record (the REQ-063 acceptance shape). Per DB-R2b the
foreign key carries the identical name as the anchor key it references —
the first cross-entity key re-appearance in the schema, sanctioned by the
registry's R2b handling in ``registry_seed``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mentorapp.storage.crm_refs import CrmEngagementRef
from mentorapp.storage.entity import BaseEntity, entity_key, entity_ref, live_index

# Free-text mentoring narrative (session summaries, note bodies) versus short
# descriptive lines (a next step, a goal). Widths, not contracts.
_NARRATIVE_LENGTH = 4000
_DESCRIPTION_LENGTH = 2000

# REQ-090 (WTK-205, wiring the WTK-204 delta design): the narrative columns
# carry clean semantic HTML from the one rich-text control — the registry
# fieldType is what routes them to ui.entry_editors.RICH_TEXT_CONTROL, never
# a UI-side list of field names. Save-time sanitization/normalization stays
# with the shared DB-S13 services, not per entry point.
_RICH_TEXT_REGISTRY = {"fieldType": "richText", "searchableFlag": True}


class MeetingNote(BaseEntity):
    """A mentor's note from a meeting — app-owned mentoring history (REQ-063)."""

    __tablename__ = "meetingNote"
    __ownership_side__ = "application"

    meeting_note_id: Mapped[uuid.UUID] = entity_key("meetingNoteID")
    meeting_note_body: Mapped[str] = mapped_column(
        "meetingNoteBody",
        String(_NARRATIVE_LENGTH),
        nullable=False,
        info={"registry": _RICH_TEXT_REGISTRY},
    )


class NextStep(BaseEntity):
    """An agreed next step coming out of mentoring work (REQ-063)."""

    __tablename__ = "nextStep"
    __ownership_side__ = "application"

    next_step_id: Mapped[uuid.UUID] = entity_key("nextStepID")
    next_step_description: Mapped[str] = mapped_column(
        "nextStepDescription",
        String(_DESCRIPTION_LENGTH),
        nullable=False,
        info={"registry": _RICH_TEXT_REGISTRY},
    )


class ProgressGoal(BaseEntity):
    """A goal progress is tracked against (REQ-063)."""

    __tablename__ = "progressGoal"
    __ownership_side__ = "application"

    progress_goal_id: Mapped[uuid.UUID] = entity_key("progressGoalID")
    progress_goal_description: Mapped[str] = mapped_column(
        "progressGoalDescription",
        String(_DESCRIPTION_LENGTH),
        nullable=False,
        info={"registry": _RICH_TEXT_REGISTRY},
    )


class SessionLog(BaseEntity):
    """One logged mentoring session, linked to its engagement's CRM record.

    The many-to-one association to :class:`CrmEngagementRef` is REQ-063's
    acceptance shape: the session persists in the application store while the
    anchor ties it to the engagement mastered in the CRM. The link is
    required — an unanchored session log would be exactly the recordless
    session tracking REQ-063 exists to end.
    """

    __tablename__ = "sessionLog"
    __ownership_side__ = "application"
    __table_args__ = (
        # The staff/mentor read "sessions for this engagement" (REQ-063:
        # staff-relevant activity stays visible), live rows only per DB-S3.
        live_index("ix_sessionLog_crmEngagementRefID_live", "crmEngagementRefID"),
    )

    session_log_id: Mapped[uuid.UUID] = entity_key("sessionLogID")
    # Re-pointing a session at a different engagement changes what the whole
    # log is about — history-track the association (DB-S5), mirroring the
    # anchors' own crm*ID columns.
    crm_engagement_ref_id: Mapped[uuid.UUID] = entity_ref(
        "crmEngagementRef.crmEngagementRefID",
        registry={"fieldLabel": "CRM Engagement Ref ID", "historyTrackedFlag": True},
    )
    session_log_date: Mapped[datetime] = mapped_column("sessionLogDate", nullable=False)
    session_log_summary: Mapped[str] = mapped_column(
        "sessionLogSummary",
        String(_NARRATIVE_LENGTH),
        nullable=False,
        info={"registry": _RICH_TEXT_REGISTRY},
    )

    crm_engagement_ref: Mapped[CrmEngagementRef] = relationship()
