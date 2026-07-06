"""CRM outage messaging & draft recovery UI design (WTK-154, REQ-064).

The UI states for the app's degraded modes — decided once, so every surface
that touches the CRM speaks the same voice when the CRM cannot. The shell
renders these verbatim; the automation-side processes (degraded reads, the
draft store — WTK-153) supply the facts they present:

- **A CRM outage is always named, never dressed up.** A CRM-backed record
  surface (preview pane, edit-screen load) resolves what it shows through
  :func:`resolve_crm_read_state`: a fresh read shows no notice, a snapshot is
  labelled with when it was captured (stale never masquerades as fresh), and
  an unavailable read says the CRM is unreachable — since when, with retry —
  never an empty pane and never a generic error. Grids already own this
  concept: a CRM-fed data source that cannot answer rides
  :func:`~mentorapp.ui.grid_panel.resolve_grid_state`'s ``dataSourceError``
  state; this resolver exists for the record surfaces grids don't cover, not
  as a second home for grid states.
- **Write failures fork on the WTK-152 disposition, and the UI never
  re-derives it.** :func:`write_failure_notice` consumes
  :func:`~mentorapp.crm.write_through.classify_crm_write_fault`'s answer:
  *transient* means the change was accepted and rides the ``crmWriteRetry``
  job — the notice says the work is safe and where completion will surface
  (change feed → notification bell); *terminal* means the CRM refused — the
  notice surfaces immediately WITH the CRM's specific cause and a retry
  affordance (deliberate resubmission of the same change). Either way the
  editor keeps the user's values on screen: :data:`FAILURE_KEEPS_EDITOR_STATE`
  is the never-lose-work rule applied to failed saves.
- **A preserved draft is offered where it was authored.** When an outage
  converts a submit into a locally preserved draft (WTK-153's
  ``submit_or_preserve``), the moment of preservation gets an educate-voice
  notice plus a notification-bell entry; re-opening the authoring surface for
  that target offers recovery (:class:`DraftRecovery.offer_for`) with
  restore/discard affordances. Restoring does NOT consume the draft — only a
  successful submit (:meth:`DraftRecovery.submit_succeeded`) or an explicit
  discard clears it, so a crash between restore and save cannot lose the work
  twice. Discard confirmations are honest about what discard does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from mentorapp.crm.write_through import WriteFaultDisposition
from mentorapp.observability import get_logger
from mentorapp.ui.auth_flows import EducateMessage

log = get_logger(__name__)


# --- CRM-backed reads: fresh / snapshot / unavailable (REQ-064) --------------------

# The read-outcome vocabulary the WTK-153 degraded-read process produces and
# this resolver presents; one spelling shared by both sides of the seam.
CRM_READ_FRESH: Final = "fresh"
CRM_READ_SNAPSHOT: Final = "snapshot"
CRM_READ_UNAVAILABLE: Final = "crmUnavailable"
CRM_READ_KINDS: Final[tuple[str, ...]] = (
    CRM_READ_FRESH,
    CRM_READ_SNAPSHOT,
    CRM_READ_UNAVAILABLE,
)


@dataclass(frozen=True)
class CrmReadNotice:
    """One rendered degraded-read state: which one, the educate triple, affordances.

    ``detail`` (the transport-level cause of an unavailable read) is available
    on request, never dumped into the message — the grid standard's
    detail-not-dumped rule, applied to record surfaces.
    """

    kind: str
    message: EducateMessage
    affordances: tuple[str, ...] = ()
    detail: str | None = None


def resolve_crm_read_state(
    surface_label: str,
    kind: str,
    *,
    captured_at: str = "",
    unavailable_since: str = "",
    reason: str | None = None,
) -> CrmReadNotice | None:
    """Decide what a CRM-backed record surface says about its data, or ``None``.

    Fresh reads render silently. A snapshot renders WITH its ``captured_at``
    label — the timestamp is the honesty, so it is required. An unavailable
    read names the CRM and ``unavailable_since`` specifically; the surface
    shows this notice instead of an empty pane, because emptiness would let
    an outage masquerade as missing data (REQ-064).
    """
    if kind == CRM_READ_FRESH:
        return None
    if kind == CRM_READ_SNAPSHOT:
        if not captured_at:
            raise ValueError("a snapshot read must carry its captured_at label")
        return CrmReadNotice(
            kind=CRM_READ_SNAPSHOT,
            message=EducateMessage(
                what_happened=f"{surface_label} is shown from a snapshot "
                f"captured {captured_at}.",
                why="The CRM system of record isn't answering right now, so the "
                "last data it confirmed is shown instead of nothing.",
                what_next="Anything changed in the CRM since then isn't reflected "
                "yet — retry to check for live data.",
            ),
            affordances=("retry",),
        )
    if kind == CRM_READ_UNAVAILABLE:
        since = f" since {unavailable_since}" if unavailable_since else ""
        return CrmReadNotice(
            kind=CRM_READ_UNAVAILABLE,
            message=EducateMessage(
                what_happened=f"{surface_label} can't be loaded.",
                why=f"The CRM system of record has been unreachable{since}, and "
                "no earlier snapshot of this data exists.",
                what_next="Retry now — if it keeps failing, the technical detail "
                "helps an administrator find the cause.",
            ),
            affordances=("retry", "showDetail"),
            detail=reason,
        )
    raise ValueError(f"unknown CRM read kind: {kind!r}")


# --- Failed CRM writes: the two dispositions, presented (REQ-064) ------------------

# The never-lose-work rule applied to a failed save: whatever the outcome
# message says, the editor behind it still holds the user's values — a failed
# write never clears, closes, or reverts the form.
FAILURE_KEEPS_EDITOR_STATE: Final = True

# Affordance vocabulary (camelCase, like every wire spelling). Terminal
# failures offer deliberate resubmission of the same change plus editing
# first; transient ones offer following the retry, not re-sending — a second
# submit would race the queued job.
TRANSIENT_WRITE_AFFORDANCES: Final[tuple[str, ...]] = ("viewRetryStatus",)
TERMINAL_WRITE_AFFORDANCES: Final[tuple[str, ...]] = ("retrySubmit", "editAndResubmit")


@dataclass(frozen=True)
class WriteFailureNotice:
    """One presented write failure: the disposition, the educate triple, affordances.

    ``crm_cause`` carries the CRM's own words for a terminal refusal — the
    specific cause REQ-064 demands — and is rendered with the message, not
    hidden behind a detail affordance: the cause IS what the user acts on.
    ``retry_job_id`` is set only on the transient path (the ``crmWriteRetry``
    job carrying the change).
    """

    disposition: WriteFaultDisposition
    message: EducateMessage
    affordances: tuple[str, ...]
    crm_cause: str | None = None
    retry_job_id: str | None = None


def write_failure_notice(
    disposition: WriteFaultDisposition,
    *,
    record_title: str,
    crm_cause: str,
    retry_job_id: str | None = None,
) -> WriteFailureNotice:
    """Present one failed CRM write, exactly as WTK-152 classified it.

    The disposition arrives from
    :func:`~mentorapp.crm.write_through.classify_crm_write_fault` — this
    function renders the fork, it never re-judges the fault. Transient
    requires the ``retry_job_id`` the request answered with; terminal
    requires the CRM's specific cause and forbids a job id (a refusal is
    never enqueued).
    """
    if disposition == "transient":
        if retry_job_id is None:
            raise ValueError("a transient write failure carries its crmWriteRetry job id")
        log.info(
            "transient write failure presented",
            extra={"context": {"retryJobId": retry_job_id, "recordTitle": record_title}},
        )
        return WriteFailureNotice(
            disposition="transient",
            message=EducateMessage(
                what_happened=f"Your change to '{record_title}' is saved and "
                "will reach the CRM automatically.",
                why="The CRM system of record couldn't take it right now, so the "
                "change is queued and retried until it lands.",
                what_next="Nothing to redo — you'll get a notification when it "
                "completes, and its status is visible until then.",
            ),
            affordances=TRANSIENT_WRITE_AFFORDANCES,
            retry_job_id=retry_job_id,
        )
    if retry_job_id is not None:
        raise ValueError("a terminal write failure is never enqueued — no job id exists")
    log.info(
        "terminal write failure presented",
        extra={"context": {"crmCause": crm_cause, "recordTitle": record_title}},
    )
    return WriteFailureNotice(
        disposition="terminal",
        message=EducateMessage(
            what_happened=f"The CRM did not accept your change to '{record_title}'.",
            why=f"It answered: {crm_cause}",
            what_next="Your edits are still on screen — adjust what the CRM "
            "objected to and submit again, or retry as-is if the "
            "objection looks wrong.",
        ),
        affordances=TERMINAL_WRITE_AFFORDANCES,
        crm_cause=crm_cause,
    )


# --- Preserved drafts: surfacing & recovery (REQ-064) ------------------------------

# Where a preserved draft announces itself: at the moment of preservation
# (educate notice + a notification-bell entry, so leaving the page doesn't
# bury the fact), and on every later re-open of the authoring surface for the
# same target until the draft is cleared.
DRAFT_SURFACING: Final[tuple[str, ...]] = (
    "onPreservation",
    "notificationBell",
    "onSurfaceOpen",
)


@dataclass(frozen=True)
class PreservedDraftRef:
    """One recoverable draft as the WTK-153 store hands it to the UI.

    Identity is ``(author_user_id, surface_key, entity_type, record_id)`` —
    the store's one-draft-per-author-and-target key, so recovery always finds
    at most one draft to offer. ``saved_at`` and ``excerpt`` are display-only:
    they let the user judge the draft before restoring it.
    """

    author_user_id: str
    surface_key: str
    entity_type: str
    record_id: str
    saved_at: str
    excerpt: str

    def identity(self) -> tuple[str, str, str, str]:
        return (self.author_user_id, self.surface_key, self.entity_type, self.record_id)


# The moment-of-preservation notice: the submit didn't land, the work did.
DRAFT_PRESERVED = EducateMessage(
    what_happened="Your work was saved as a draft instead of being submitted.",
    why="The CRM system of record isn't reachable right now, so submitting "
    "wasn't possible — but nothing you wrote was lost.",
    what_next="You'll find this draft right here when you come back; restore "
    "it and submit once the CRM is available again.",
)


def draft_recovery_offer(draft: PreservedDraftRef) -> EducateMessage:
    """The re-open offer: names when the draft was preserved and both ways out."""
    return EducateMessage(
        what_happened=f"A draft from {draft.saved_at} was preserved here.",
        why="It was saved automatically when a submit couldn't reach the CRM, "
        "so your earlier work is intact.",
        what_next="Restore it to pick up where you left off, or discard it to start fresh.",
    )


def discard_draft_confirmation(draft: PreservedDraftRef) -> EducateMessage:
    """The discard confirmation — honest wording, per the soft-delete rule.

    Drafts follow the system-wide soft delete: discard removes the draft from
    recovery everywhere, and an administrator can restore it — the wording
    says exactly that, never "cannot be undone".
    """
    return EducateMessage(
        what_happened=f"Discard the draft from {draft.saved_at}?",
        why="Discarding removes it from recovery here and everywhere else it is offered.",
        what_next="An administrator can restore a discarded draft if you change "
        "your mind — or keep it by choosing Restore instead.",
    )


class UnknownDraftError(Exception):
    """A draft identity the controller was never given — a caller bug."""


class DraftRecovery:
    """Reference recovery behavior over the WTK-153 draft store (the shell renders it).

    Owns the two REQ-064 recovery invariants: opening an authoring surface
    whose target has a preserved draft always offers it (never silently
    overwrites the editor, never silently drops the draft), and a draft
    outlives its own restoration — it clears only on a successful submit or
    an explicit discard, so the preserved copy survives anything that happens
    to the editor in between.
    """

    def __init__(self, drafts: tuple[PreservedDraftRef, ...] = ()) -> None:
        self._drafts: dict[tuple[str, str, str, str], PreservedDraftRef] = {
            draft.identity(): draft for draft in drafts
        }

    def draft_preserved(self, draft: PreservedDraftRef) -> EducateMessage:
        """Register a just-preserved draft; same identity upserts, never duplicates."""
        self._drafts[draft.identity()] = draft
        log.info(
            "draft preserved for recovery",
            extra={"context": {"surfaceKey": draft.surface_key, "recordId": draft.record_id}},
        )
        return DRAFT_PRESERVED

    def recoverable(self) -> tuple[PreservedDraftRef, ...]:
        """Every draft currently offered for recovery (feeds the bell and lists)."""
        return tuple(self._drafts.values())

    def offer_for(
        self, author_user_id: str, surface_key: str, entity_type: str, record_id: str
    ) -> tuple[PreservedDraftRef, EducateMessage] | None:
        """The draft to offer when this authoring surface opens, or ``None``."""
        draft = self._drafts.get((author_user_id, surface_key, entity_type, record_id))
        if draft is None:
            return None
        return draft, draft_recovery_offer(draft)

    def restore(self, draft: PreservedDraftRef) -> PreservedDraftRef:
        """Hand the draft to the editor — WITHOUT consuming it.

        The preserved copy stays until :meth:`submit_succeeded` or
        :meth:`discard`: restoring is a read, so a crash after restore still
        finds the draft waiting.
        """
        known = self._drafts.get(draft.identity())
        if known is None:
            raise UnknownDraftError(draft.identity())
        return known

    def submit_succeeded(self, draft: PreservedDraftRef) -> None:
        """A submit for this target landed — the draft has served and clears.

        Clearing an already-cleared draft is fine: the submit, not the clear,
        is the event, and it may fan out from more than one window.
        """
        if self._drafts.pop(draft.identity(), None) is not None:
            log.info(
                "draft cleared by successful submit",
                extra={"context": {"surfaceKey": draft.surface_key}},
            )

    def discard(self, draft: PreservedDraftRef) -> None:
        """The user's explicit, confirmed discard; an unknown draft is a caller bug."""
        if draft.identity() not in self._drafts:
            raise UnknownDraftError(draft.identity())
        del self._drafts[draft.identity()]
        log.info(
            "draft discarded by user",
            extra={"context": {"surfaceKey": draft.surface_key, "recordId": draft.record_id}},
        )
