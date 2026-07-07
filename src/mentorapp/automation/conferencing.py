"""Conference meeting creation: the org-account provider seam (WTK-170, REQ-078/080).

Doug's 2026-07-06 ruling on REQ-078/REQ-080: scheduling a session sends the
client a meeting invite carrying the conference link, and the automated path
creates the meeting under the CBM ORG accounts — the confirmed production
provider is org-hosted Zoom via a server-to-server OAuth app, with a Google
Workspace service account (Meet) as the alternate. Which one binds is a
deployment decision made at deployment wiring time; NEITHER is called from
this repository — no real external calls, no metered API usage, exactly the
Espo-gateway seam stance (the surface owns policy, the provider owns the
platform).

- :class:`ConferencingProvider` is the Protocol the session-create endpoint
  depends on: one ``create_meeting`` taking the session's context and
  answering the join URL plus the platform's own meeting identifier. The
  external identifier is what the transcript automation later addresses the
  platform with (REQ-083 — :mod:`mentorapp.automation.transcripts`), which is
  why it is part of the contract and persisted on the session row.
- :class:`FakeConferencingProvider` is the sanctioned dev default (the
  ``LoggedEmailTransport`` stance, not the fail-loud catalog stance): it
  answers deterministic URLs derived from the session context and logs, so
  the REQ-078 scheduling flow works end to end in development and tests
  assert on stable values. Binding Zoom/Meet is a ``dependency_overrides``
  install against :func:`mentorapp.api.routers.mentoring.get_conferencing_provider`.

The REQ-079 paste-a-link path is the universal fallback and stays untouched:
a pasted link simply bypasses this seam (no org meeting exists, so no
external meeting identifier and no transcript automation — the paste paths
cover both).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from mentorapp.observability import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class MeetingContext:
    """What a provider needs to create one session's meeting.

    ``engagement_id`` + ``scheduled_at`` identify the meeting deterministically
    before the session row exists (the create endpoint books the meeting and
    persists the session in one write); the names travel so a real provider
    can title the meeting and invite the participants on the platform side.
    """

    engagement_id: uuid.UUID
    engagement_name: str
    contact_name: str
    contact_email: str
    scheduled_at: datetime


@dataclass(frozen=True)
class CreatedMeeting:
    """One created org meeting: where to join, and the platform's own identifier.

    ``external_meeting_id`` is the handle the transcript retrieval (REQ-083)
    later presents back to the platform — it is persisted as the session's
    ``externalMeetingID``.
    """

    join_url: str
    external_meeting_id: str


class ConferencingProvider(Protocol):
    """The meeting-creation seam (REQ-080): platform binding is deployment wiring.

    ``create_meeting`` either books the meeting on the org account and
    answers it, or raises — the endpoint treats a raise as "the automated
    path is unavailable" and educates toward the REQ-079 paste path rather
    than failing the scheduling silently.
    """

    def create_meeting(self, context: MeetingContext) -> CreatedMeeting:
        """Book one org-hosted meeting for one session."""
        ...


@dataclass(frozen=True)
class FakeConferencingProvider:
    """The dev/test provider: deterministic URLs, logs, never a real booking.

    The join URL and external identifier derive from the meeting context
    (engagement + minute-precision start), so a rescheduled session books a
    DIFFERENT meeting while a retried identical request books the same one —
    the shape a real provider's idempotent booking gives. ``.invalid`` is the
    reserved TLD, so a dev link can never resolve to a real host.
    """

    base_url: str = "https://conference.dev.invalid"

    def create_meeting(self, context: MeetingContext) -> CreatedMeeting:
        slug = f"{context.engagement_id.hex[:12]}-{context.scheduled_at:%Y%m%d%H%M}"
        meeting = CreatedMeeting(
            join_url=f"{self.base_url}/m/{slug}",
            external_meeting_id=f"dev-meeting-{slug}",
        )
        log.info(
            "org meeting created (dev provider — not a real booking)",
            extra={
                "context": {
                    "engagementID": str(context.engagement_id),
                    "externalMeetingID": meeting.external_meeting_id,
                    "scheduledAt": context.scheduled_at.isoformat(),
                }
            },
        )
        return meeting
