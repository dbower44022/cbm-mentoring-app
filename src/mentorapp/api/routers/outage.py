"""``/outage`` — REQ-064's degraded-mode wire surface (WTK-159).

The build of the WTK-154 design over the WTK-153 processes and the WTK-157
write outcomes: no frontend shell exists yet (PI-002), so
``mentorapp.ui.outage_recovery`` stays the one home for the *wording* (the
educate messages, affordance vocabulary, and recovery invariants) and this
module is the wire shape a shell renders when the CRM cannot answer.

- :func:`crm_read_payload` — the one serialization of a
  :data:`~mentorapp.automation.crm_outage.CrmReadResult` for CRM-backed read
  endpoints: fresh rides silently, a snapshot carries its label, an
  unavailable read carries the specific since-when and reason — an outage
  can never reach the wire as an empty result.
- :func:`write_failure_payload` / :func:`draft_preserved_payload` — the
  presentations write and submit endpoints answer with: the WTK-152
  disposition fork rendered without re-judging it (transient = queued job id
  + follow-the-retry; terminal = the CRM's cause + the resubmit affordances),
  and the moment-of-preservation notice when an outage converted a submit
  into a preserved draft.
- ``GET /outage/drafts`` / ``GET /outage/drafts/{kind}/{ref}`` /
  ``POST /outage/drafts/{kind}/{ref}/discard`` — draft recovery over the
  WTK-153 :class:`~mentorapp.automation.crm_outage.DraftStore`. The offer is
  a read (restoring never consumes the draft — only a successful submit or
  this explicit discard clears it), and every draft is scoped to the session
  user: the store is keyed by author, so no request can see or discard
  another author's work.

The draft-store provider follows the records-router seam pattern (fail
loudly until wired; tests and deployments override :func:`get_draft_store`):
the durable table lands with its storage planning item, and an empty
in-process default would make every preserved draft silently unrecoverable.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from mentorapp.api.crm_writes import WriteDeferred, WriteRefused
from mentorapp.api.deps import get_current_user_id
from mentorapp.api.envelope import Envelope, ok
from mentorapp.automation.crm_outage import (
    CrmReadResult,
    DraftKey,
    DraftPreserved,
    DraftStore,
    FreshRead,
    PreservedDraft,
    StaleRead,
    discard_draft,
    recoverable_drafts,
)
from mentorapp.observability import get_logger
from mentorapp.ui.outage_recovery import (
    CRM_READ_FRESH,
    CRM_READ_SNAPSHOT,
    CRM_READ_UNAVAILABLE,
    DRAFT_PRESERVED,
    DRAFT_SURFACING,
    FAILURE_KEEPS_EDITOR_STATE,
    PreservedDraftRef,
    discard_draft_confirmation,
    draft_recovery_offer,
    resolve_crm_read_state,
    write_failure_notice,
)

log = get_logger(__name__)

router = APIRouter()

# How much of a draft the recovery offer shows: enough to judge the work
# before restoring it, small enough to ride every list response.
_EXCERPT_LENGTH = 120


def _wire_instant(value: datetime) -> str:
    return value.isoformat()


# --- CRM-backed reads: the degraded-read shapes, serialized (REQ-064) --------------


def crm_read_payload(surface_label: str, result: CrmReadResult) -> dict[str, Any]:
    """Serialize one degraded-read outcome for a CRM-backed read endpoint.

    Maps the WTK-153 shapes through the WTK-154 resolver — this function
    formats, it never re-judges freshness. Fresh answers carry ``notice:
    None`` (nothing to explain); a snapshot carries its ``capturedAt`` label
    and an unavailable read its ``unavailableSince``, each with the educate
    notice the shell renders instead of an empty pane. ``result.data`` stays
    the caller's to place — payload shape is the endpoint's contract.
    """
    if isinstance(result, FreshRead):
        return {
            "state": CRM_READ_FRESH,
            "notice": None,
            "capturedAt": None,
            "unavailableSince": None,
        }
    if isinstance(result, StaleRead):
        kind = CRM_READ_SNAPSHOT
        captured_at = _wire_instant(result.captured_at)
        notice = resolve_crm_read_state(surface_label, kind, captured_at=captured_at)
    else:
        kind = CRM_READ_UNAVAILABLE
        captured_at = None
        notice = resolve_crm_read_state(
            surface_label,
            kind,
            unavailable_since=(
                _wire_instant(result.unavailable_since) if result.unavailable_since else ""
            ),
            reason=result.reason,
        )
    unavailable_since = (
        _wire_instant(result.unavailable_since) if result.unavailable_since else None
    )
    return {
        "state": kind,
        "notice": None
        if notice is None
        else {
            "message": notice.message.as_payload(),
            "affordances": list(notice.affordances),
            "detail": notice.detail,
        },
        "capturedAt": captured_at,
        "unavailableSince": unavailable_since,
    }


# --- Failed CRM writes: the disposition fork, serialized (REQ-064) -----------------


def write_failure_payload(
    outcome: WriteDeferred | WriteRefused, *, record_title: str
) -> dict[str, Any]:
    """Serialize one failed CRM write exactly as WTK-157 answered it.

    Transient (:class:`WriteDeferred`) carries the ``crmWriteRetry`` job id
    and the follow-the-retry affordance; terminal (:class:`WriteRefused`)
    carries the CRM's specific cause in the message plus the deliberate
    resubmission affordances. ``keepsEditorState`` rides every failure: the
    shell never clears the form on a failed save, whichever fork answered.
    """
    if isinstance(outcome, WriteDeferred):
        notice = write_failure_notice(
            "transient",
            record_title=record_title,
            crm_cause=outcome.reason,
            retry_job_id=str(outcome.retry_job_id),
        )
    else:
        notice = write_failure_notice(
            "terminal", record_title=record_title, crm_cause=outcome.crm_cause
        )
    return {
        "disposition": notice.disposition,
        "message": notice.message.as_payload(),
        "affordances": list(notice.affordances),
        "crmCause": notice.crm_cause,
        "retryJobId": notice.retry_job_id,
        "keepsEditorState": FAILURE_KEEPS_EDITOR_STATE,
    }


# --- Preserved drafts: surfacing & recovery over the WTK-153 store (REQ-064) -------


def get_draft_store() -> DraftStore:
    """Provide the preserved-draft store; wiring binds it, tests override it.

    Fail-loud, never an empty default: a missing binding must read as a
    deployment error, not as every preserved draft in the app being gone.
    """
    raise RuntimeError(
        "draft store provider is not wired; install outage wiring or override get_draft_store."
    )


_UserDep = Annotated[uuid.UUID, Depends(get_current_user_id)]
_StoreDep = Annotated[DraftStore, Depends(get_draft_store)]


def draft_excerpt(content: Mapping[str, Any]) -> str:
    """The first human-readable value in a draft, truncated for the offer."""
    for value in content.values():
        if isinstance(value, str) and value.strip():
            text = value.strip()
            return text if len(text) <= _EXCERPT_LENGTH else text[: _EXCERPT_LENGTH - 1] + "…"
    return ""


def _draft_ref(draft: PreservedDraft) -> PreservedDraftRef:
    # The WTK-153 store keys a draft by (author, targetKind, targetRef); the
    # WTK-154 display ref speaks (surface, entityType, recordId). Until a
    # dedicated surface vocabulary exists, the target kind names both the
    # authoring surface and the entity type — the two vocabularies are
    # aligned here and nowhere else.
    return PreservedDraftRef(
        author_user_id=draft.key.author_user_id,
        surface_key=draft.key.target_kind,
        entity_type=draft.key.target_kind,
        record_id=draft.key.target_ref,
        saved_at=_wire_instant(draft.preserved_at),
        excerpt=draft_excerpt(draft.content),
    )


def _draft_payload(draft: PreservedDraft) -> dict[str, Any]:
    ref = _draft_ref(draft)
    return {
        "targetKind": draft.key.target_kind,
        "targetRef": draft.key.target_ref,
        "content": dict(draft.content),
        "excerpt": ref.excerpt,
        "firstPreservedAt": _wire_instant(draft.first_preserved_at),
        "preservedAt": _wire_instant(draft.preserved_at),
        "reason": draft.reason,
        "offer": draft_recovery_offer(ref).as_payload(),
        "discardConfirmation": discard_draft_confirmation(ref).as_payload(),
    }


def draft_preserved_payload(outcome: DraftPreserved) -> dict[str, Any]:
    """Serialize the moment of preservation: the submit didn't land, the work did.

    Submit endpoints answer with this when ``submit_or_preserve`` preserved:
    the DRAFT_PRESERVED educate notice, the full draft (so the editor keeps
    rendering the preserved values), and the WTK-154 surfacing contract the
    shell honors (notice now, bell entry, offer on every later re-open).
    """
    return {
        "notice": DRAFT_PRESERVED.as_payload(),
        "surfacing": list(DRAFT_SURFACING),
        "draft": _draft_payload(outcome.draft),
        "unavailableSince": (
            _wire_instant(outcome.unavailable_since) if outcome.unavailable_since else None
        ),
        "reason": outcome.reason,
    }


@router.get("/outage/drafts")
def list_preserved_drafts(user_id: _UserDep, store: _StoreDep) -> Envelope:
    """The session user's recoverable drafts, most recently touched first.

    Feeds the recovery list and the notification bell. Every entry carries
    its recovery offer and its honest discard confirmation, so the shell
    renders wording from one home. 401 without a live session reference
    (FND-909 D9); fails loudly
    when the store provider is unwired.
    """
    drafts = recoverable_drafts(store, str(user_id))
    return ok(
        data={"drafts": [_draft_payload(draft) for draft in drafts]},
        meta={"count": len(drafts)},
    )


@router.get("/outage/drafts/{target_kind}/{target_ref}")
def get_draft_offer(
    target_kind: str, target_ref: str, user_id: _UserDep, store: _StoreDep
) -> Envelope:
    """The draft to offer when this authoring surface opens, or ``data.draft: None``.

    A clean surface is the normal case, not an error — the shell opens the
    editor empty on ``None`` and renders the offer otherwise. Reading the
    offer never consumes the draft (the REQ-064 invariant): the preserved
    copy survives until a successful submit or an explicit discard.
    """
    key = DraftKey(author_user_id=str(user_id), target_kind=target_kind, target_ref=target_ref)
    draft = store.get(key)
    return ok(data={"draft": _draft_payload(draft) if draft is not None else None})


@router.post("/outage/drafts/{target_kind}/{target_ref}/discard")
def discard_preserved_draft(
    target_kind: str, target_ref: str, user_id: _UserDep, store: _StoreDep
) -> Envelope:
    """The author's explicit, confirmed discard of one preserved draft.

    Follows the WTK-153 store contract: a double discard (submit raced with
    a manual discard, or two windows) is a no-op, not an error — ``data.
    discarded`` says whether this call removed anything. The confirmation
    wording the shell shows first rides the offer payload.
    """
    key = DraftKey(author_user_id=str(user_id), target_kind=target_kind, target_ref=target_ref)
    existed = store.get(key) is not None
    discard_draft(store, key)
    if existed:
        log.info(
            "preserved draft discarded by its author",
            extra={
                "context": {
                    "userId": str(user_id),
                    "targetKind": target_kind,
                    "targetRef": target_ref,
                }
            },
        )
    return ok(data={"discarded": existed})
