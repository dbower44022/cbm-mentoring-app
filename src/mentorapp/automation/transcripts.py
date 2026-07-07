"""Transcript retrieval and AI-draft seams (WTK-180/181, REQ-083).

REQ-083's automation half: after a call on an APP-CREATED meeting (the
session row carries ``externalMeetingID`` — :mod:`.conferencing`), the
transcript is retrieved from the conference platform, attached to the
session, and a summary plus suggested action items are PRE-DRAFTED for the
mentor to review. This module owns the two seams and the append-only merge
rule; the attach itself and the ``transcriptRetrieval`` job handler live in
the API layer (:mod:`mentorapp.api.routers.mentoring`) because they ride the
one write engine, which this layer sits below (storage → automation → api).

- :class:`TranscriptSource` retrieves the platform's AI transcript by the
  meeting's external identifier. Production is the same platform the
  conferencing provider booked on (org-hosted Zoom's transcript API; Google
  Meet as the alternate) — a deployment-wiring binding; this repo makes NO
  real external calls. ``None`` means "not produced yet", a transient state
  the job path retries with backoff.
- :class:`SummaryDrafter` turns a transcript into the two draft proposals.
  Production adoption of a concrete AI model/provider is a design-time pick
  made at deployment; this repository deliberately ships only the
  deterministic fake — NO metered API calls ever run from here.
- :func:`extended_transcript` is the one append-only merge rule (WTK-182):
  what ``transcriptText`` may become given what is already stored — the
  model's own guard enforces it; this function is how every writer satisfies
  it.

Authorship (REQ-083): drafts are PROPOSALS on ``draftSummary``/
``draftActionItems`` that the mentor accepts or edits INTO ``sessionNotes``/
``actionItems`` through the normal session PATCH. Automation never writes
the mentor's notes or action items — the mentor remains the author of
record. The REQ-083 paste path (mentor pastes a transcript into the session
PATCH) covers meetings the automation cannot reach.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from html import escape
from typing import Final, Protocol

from mentorapp.observability import get_logger

log = get_logger(__name__)

# The queue vocabulary for the automated retrieval (one meaning system-wide).
TRANSCRIPT_RETRIEVAL_JOB_TYPE: Final = "transcriptRetrieval"

# When the automated retrieval first tries, measured from the session's
# scheduled START (sessions carry no duration): long enough that a typical
# session has ended and the platform has processed its transcript, short
# enough that the drafts await the mentor the same day. A too-early attempt
# is harmless — "not produced yet" retries on the worker's backoff.
TRANSCRIPT_RETRIEVAL_DELAY: Final = timedelta(hours=2)


@dataclass(frozen=True)
class RetrievedTranscript:
    """One retrieved transcript plus its provenance label.

    ``transcript_source`` is what the session's ``transcriptSource`` column
    records (which tool produced it) so retention questions stay answerable
    (WTK-182).
    """

    transcript_text: str
    transcript_source: str


class TranscriptSource(Protocol):
    """Where a meeting's AI transcript comes from (REQ-083).

    ``retrieve`` answers ``None`` while the platform has not produced the
    transcript yet (a transient state), and raises only on a platform
    failure — the job path maps a raise to a retry, the endpoint to a
    refusal in the educate voice.
    """

    def retrieve(self, external_meeting_id: str) -> RetrievedTranscript | None:
        """The transcript of one app-created meeting, or ``None`` if not ready."""
        ...


@dataclass(frozen=True)
class FakeTranscriptSource:
    """The dev/test source: a deterministic transcript, logged, never fetched.

    Same sanctioned-default stance as
    :class:`~mentorapp.automation.conferencing.FakeConferencingProvider` —
    the REQ-083 flow works end to end in development with stable content
    tests can assert on.
    """

    def retrieve(self, external_meeting_id: str) -> RetrievedTranscript | None:
        transcript = (
            f"[dev transcript for {external_meeting_id}]\n"
            "Mentor: Let's review progress since last time.\n"
            "Client: We shipped the pricing page and enrollment grew.\n"
            "ACTION: Client drafts the Q3 hiring plan.\n"
            "ACTION: Mentor shares the cash-flow template."
        )
        log.info(
            "transcript retrieved (dev source — deterministic content)",
            extra={"context": {"externalMeetingID": external_meeting_id}},
        )
        return RetrievedTranscript(
            transcript_text=transcript, transcript_source="devTranscript"
        )


@dataclass(frozen=True)
class DraftProposal:
    """The two REQ-083 proposals, as clean HTML for the rich-text entry fields."""

    draft_summary: str
    draft_action_items: str


class SummaryDrafter(Protocol):
    """The AI drafting seam (REQ-083): transcript in, two HTML proposals out.

    Production binds a concrete model at deployment (a design-time pick);
    nothing in this repository calls a metered API.
    """

    def draft(self, transcript_text: str) -> DraftProposal:
        """Draft a summary and suggested action items from one transcript."""
        ...


# The fake drafter's action-item convention: transcript lines that name an
# agreed action. Chosen because it is trivially deterministic, not because
# real transcripts carry it — the production drafter owns real extraction.
_ACTION_LINE: Final = re.compile(r"^\s*ACTION:\s*(?P<item>.+)$", re.MULTILINE)


@dataclass(frozen=True)
class FakeSummaryDrafter:
    """The dev/test drafter: deterministic extraction, no model anywhere.

    Summary = the transcript's first content lines joined; action items =
    the ``ACTION:`` lines as a bulleted list. Both are wrapped as the clean
    semantic HTML the rich-text fields carry (REQ-090), so accepting a draft
    into the entry editors needs no conversion.
    """

    summary_line_count: int = 2

    def draft(self, transcript_text: str) -> DraftProposal:
        lines = [line.strip() for line in transcript_text.splitlines() if line.strip()]
        content = [line for line in lines if not line.startswith("[")]
        summary = " ".join(content[: self.summary_line_count]) or "No transcript content."
        items = [
            match.group("item").strip() for match in _ACTION_LINE.finditer(transcript_text)
        ]
        bullets = "".join(f"<li>{escape(item)}</li>" for item in items)
        return DraftProposal(
            draft_summary=f"<p>{escape(summary)}</p>",
            draft_action_items=f"<ul>{bullets}</ul>" if bullets else "<ul></ul>",
        )


def extended_transcript(current: str | None, retrieved: str) -> str | None:
    """The append-only merge: what ``transcriptText`` becomes, or ``None`` for no-op.

    The model guard (WTK-182) admits only values that START WITH the stored
    transcript, so: no stored transcript → the retrieved text; a retrieval
    that extends the stored text → the retrieved text; identical or shorter
    (a replay) → no-op; a DIVERGENT retrieval is appended after a separator —
    evidence is kept, never rewritten, and the result still passes the guard.
    """
    if current is None or not current:
        return retrieved
    if retrieved == current or current.startswith(retrieved):
        return None
    if retrieved.startswith(current):
        return retrieved
    return f"{current}\n\n{retrieved}"
