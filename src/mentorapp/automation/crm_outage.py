"""CRM outage degradation and draft preservation (REQ-064, WTK-153).

Two processes, designed as code, that keep the app up and honest while the
CRM is unreachable:

- ``crm_outage_degradation`` — every CRM-dependent read answers through
  :func:`degraded_crm_read`, which returns exactly one of three honest
  shapes: :class:`FreshRead` (the CRM answered now), :class:`StaleRead`
  (a held snapshot, LABELLED with when it was true), or
  :class:`UnavailableRead` (a specific "the CRM cannot answer" with the
  reason and since-when). An outage can never surface as an empty result,
  and a snapshot can never surface as fresh — the shapes are distinct
  types, not flags a caller may forget to check. A :class:`CrmHealthMonitor`
  (a plain consecutive-failure circuit breaker) short-circuits reads during
  a sustained outage so every request stops re-paying the transport timeout,
  then lets one probe through per cooldown to notice recovery. Sustained
  SLOWNESS needs no second vocabulary: the transport's bounded timeout
  (``crm.http``) already converts a CRM that is too slow into
  :class:`~mentorapp.crm.auth.CrmUnavailableError`, so one failure signal
  covers down and crawling alike — deliberately boring, per the REQ-064
  volume context (one volunteer ops engineer).

- ``crm_draft_preservation`` — :func:`submit_or_preserve` wraps a CRM-bound
  submit so an outage converts in-progress work into a local
  :class:`PreservedDraft` instead of losing it: one draft per
  :class:`DraftKey` (author + target), idempotently updated on every
  re-preserve, never duplicated. :func:`recoverable_drafts` and
  :func:`discard_draft` drive the recovery surface (WTK-154 designs its
  presentation). Draft persistence sits behind the narrow
  :class:`DraftStore` Protocol with :class:`InMemoryDraftStore` as the
  reference implementation — the WTK-002 seam pattern, so the storage-area
  design binds a durable table underneath without process changes.

Boundary with WTK-152 (CRM write-through/write-retry, api area): a draft
holds work the user has NOT successfully handed over; retry of accepted
writes, transient-vs-terminal classification, and idempotency keys belong
to the write path. Only :class:`~mentorapp.crm.auth.CrmUnavailableError`
preserves a draft here — every other submit failure propagates untouched
for the write contract to judge.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Protocol

from mentorapp.crm.auth import CrmUnavailableError
from mentorapp.observability import get_logger
from mentorapp.storage import utcnow

logger = get_logger(__name__)

# Breaker defaults: open after three straight failures (one flake is not an
# outage), probe again after thirty seconds. Both are constructor knobs; the
# defaults are the deployment values.
FAILURE_THRESHOLD = 3
PROBE_COOLDOWN = timedelta(seconds=30)


@dataclass(frozen=True)
class CrmAvailability:
    """The monitor's current judgement, for banners and the status surface.

    ``unavailable_since`` and ``reason`` are set exactly when ``available``
    is False, so a banner can say since-when and why, not just "down".
    """

    available: bool
    unavailable_since: datetime | None = None
    reason: str | None = None


class CrmHealthMonitor:
    """Consecutive-failure circuit breaker over the CRM binding.

    Closed (available) until ``failure_threshold`` straight failures, then
    open: :meth:`allow_attempt` answers False so callers short-circuit to
    their degraded shape without re-paying the transport timeout. Once per
    ``probe_cooldown`` it answers True again — one live probe — and a
    recorded success closes the breaker. State transitions are logged; the
    judgement itself is served by :meth:`availability`.

    Not thread-safe by design: one monitor lives per worker process next to
    the binding it watches, the same singleton shape as the engine plug.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = FAILURE_THRESHOLD,
        probe_cooldown: timedelta = PROBE_COOLDOWN,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._probe_cooldown = probe_cooldown
        self._consecutive_failures = 0
        self._unavailable_since: datetime | None = None
        self._last_probe_at: datetime | None = None
        self._reason: str | None = None

    def availability(self) -> CrmAvailability:
        """The current judgement: available, or since-when and why not."""
        if self._unavailable_since is None:
            return CrmAvailability(available=True)
        return CrmAvailability(
            available=False,
            unavailable_since=self._unavailable_since,
            reason=self._reason,
        )

    def allow_attempt(self, *, now: datetime | None = None) -> bool:
        """Whether a live CRM call should be made right now.

        True while closed; while open, True once per cooldown (the probe)
        and False otherwise. Callers answering False serve their degraded
        shape immediately.
        """
        if self._unavailable_since is None:
            return True
        now = now or utcnow()
        anchor = self._last_probe_at or self._unavailable_since
        if now - anchor >= self._probe_cooldown:
            self._last_probe_at = now
            return True
        return False

    def record_success(self) -> None:
        """A live call answered: reset the count and close the breaker."""
        if self._unavailable_since is not None:
            logger.info(
                "CRM recovered; closing outage breaker",
                extra={"context": {"unavailableSince": self._unavailable_since.isoformat()}},
            )
        self._consecutive_failures = 0
        self._unavailable_since = None
        self._last_probe_at = None
        self._reason = None

    def record_failure(self, reason: str, *, now: datetime | None = None) -> None:
        """A live call could not be answered; open after the threshold."""
        self._consecutive_failures += 1
        self._reason = reason
        if (
            self._unavailable_since is None
            and self._consecutive_failures >= self._failure_threshold
        ):
            self._unavailable_since = now or utcnow()
            logger.warning(
                "CRM unavailable; opening outage breaker",
                extra={
                    "context": {
                        "consecutiveFailures": self._consecutive_failures,
                        "reason": reason,
                    }
                },
            )


@dataclass(frozen=True)
class FreshRead:
    """The CRM answered this read now; ``data`` is current."""

    data: Any


@dataclass(frozen=True)
class StaleRead:
    """A held snapshot served during an outage — true as of ``captured_at``.

    A distinct type from :class:`FreshRead` so staleness cannot be dropped
    on the floor: the read surface renders it WITH its age (REQ-064 forbids
    stale-as-fresh), alongside the outage explanation.
    """

    data: Any
    captured_at: datetime
    unavailable_since: datetime | None
    reason: str


@dataclass(frozen=True)
class UnavailableRead:
    """No data can be served: the CRM is unavailable and no snapshot is held.

    Specifically NOT an empty result — ``reason`` and ``unavailable_since``
    are what the read surface shows instead of a blank list.
    """

    unavailable_since: datetime | None
    reason: str


CrmReadResult = FreshRead | StaleRead | UnavailableRead


@dataclass(frozen=True)
class CrmSnapshot:
    """A caller-held last-good copy: the data and when it was true."""

    data: Any
    captured_at: datetime


def degraded_crm_read(
    monitor: CrmHealthMonitor,
    read: Callable[[], Any],
    *,
    snapshot: CrmSnapshot | None = None,
    now: datetime | None = None,
) -> CrmReadResult:
    """The one degradation path for CRM-dependent reads (crm_outage_degradation).

    Attempts ``read`` when the monitor allows it; a breaker that is open
    (and not due a probe) short-circuits without touching the network. On
    :class:`CrmUnavailableError` — or a short-circuit — the answer is the
    caller's ``snapshot`` labelled stale, or :class:`UnavailableRead` when
    none is held. The reason is the live failure's own when one happened
    (specific, per REQ-064), the breaker's stored one on a short-circuit.
    Non-outage exceptions from ``read`` propagate untouched: a CRM refusal
    is a real answer, not degradation.
    """
    reason: str | None = None
    if monitor.allow_attempt(now=now):
        try:
            data = read()
        except CrmUnavailableError as exc:
            reason = str(exc) or "CRM did not answer"
            monitor.record_failure(reason, now=now)
        else:
            monitor.record_success()
            return FreshRead(data=data)
    state = monitor.availability()
    reason = reason or state.reason or "The CRM is not answering."
    if snapshot is not None:
        return StaleRead(
            data=snapshot.data,
            captured_at=snapshot.captured_at,
            unavailable_since=state.unavailable_since,
            reason=reason,
        )
    return UnavailableRead(unavailable_since=state.unavailable_since, reason=reason)


@dataclass(frozen=True)
class DraftKey:
    """What a draft is anchored to: one author's in-progress work on one target.

    ``target_ref`` is the record's ID for edit drafts; create drafts carry
    the caller-minted placeholder ID (the app assigns entity keys client-side
    as UUIDv7), so two unsaved new records never collide on one key.
    """

    author_user_id: str
    target_kind: str
    target_ref: str


@dataclass(frozen=True)
class PreservedDraft:
    """One preserved unit of in-progress work, replaced wholesale per key.

    Last-write-wins per key — a draft has exactly one author, so merge
    machinery would be dead weight. ``first_preserved_at`` survives updates
    so recovery can say how long the work has been waiting.
    """

    key: DraftKey
    content: Mapping[str, Any]
    first_preserved_at: datetime
    preserved_at: datetime
    reason: str


class DraftStore(Protocol):
    """Where preserved drafts live — local by definition (REQ-064): the store
    must not depend on the CRM being reachable. The storage-area design binds
    a durable table here; processes stay unchanged.
    """

    def get(self, key: DraftKey) -> PreservedDraft | None: ...

    def upsert(self, draft: PreservedDraft) -> None: ...

    def list_for_author(self, author_user_id: str) -> list[PreservedDraft]: ...

    def discard(self, key: DraftKey) -> None: ...


class InMemoryDraftStore:
    """Reference :class:`DraftStore`: one dict, keyed exactly like the seam."""

    def __init__(self) -> None:
        self._drafts: dict[DraftKey, PreservedDraft] = {}

    def get(self, key: DraftKey) -> PreservedDraft | None:
        return self._drafts.get(key)

    def upsert(self, draft: PreservedDraft) -> None:
        self._drafts[draft.key] = draft

    def list_for_author(self, author_user_id: str) -> list[PreservedDraft]:
        return [d for d in self._drafts.values() if d.key.author_user_id == author_user_id]

    def discard(self, key: DraftKey) -> None:
        self._drafts.pop(key, None)


def preserve_draft(
    store: DraftStore,
    key: DraftKey,
    content: Mapping[str, Any],
    *,
    reason: str,
    now: datetime | None = None,
) -> PreservedDraft:
    """Preserve (or re-preserve) one unit of in-progress work — idempotent.

    Upserts by key: repeated preservation of the same work updates the one
    draft in place (content replaced wholesale, ``preserved_at`` bumped,
    ``first_preserved_at`` kept), never duplicates it.
    """
    now = now or utcnow()
    existing = store.get(key)
    if existing is not None:
        draft = replace(existing, content=dict(content), preserved_at=now, reason=reason)
    else:
        draft = PreservedDraft(
            key=key,
            content=dict(content),
            first_preserved_at=now,
            preserved_at=now,
            reason=reason,
        )
    store.upsert(draft)
    logger.info(
        "draft preserved during CRM unavailability",
        extra={
            "context": {
                "targetKind": key.target_kind,
                "targetRef": key.target_ref,
                "updatedExisting": existing is not None,
            }
        },
    )
    return draft


def recoverable_drafts(store: DraftStore, author_user_id: str) -> list[PreservedDraft]:
    """The author's preserved drafts, most recently touched first.

    The recovery surface (WTK-154) lists these when the author returns;
    resuming one re-enters the normal edit-and-submit path.
    """
    drafts = store.list_for_author(author_user_id)
    return sorted(drafts, key=lambda d: d.preserved_at, reverse=True)


def discard_draft(store: DraftStore, key: DraftKey) -> None:
    """Drop a draft: after its work was successfully submitted, or on the
    author's explicit discard. Absent keys are a no-op — a double discard
    (submit raced with a manual discard) is not an error.
    """
    store.discard(key)


@dataclass(frozen=True)
class SubmitAccepted:
    """The CRM took the write; ``result`` is the submit callable's answer."""

    result: Any


@dataclass(frozen=True)
class DraftPreserved:
    """The CRM could not take the write; the work is safe as ``draft``."""

    draft: PreservedDraft
    unavailable_since: datetime | None
    reason: str


SubmitOutcome = SubmitAccepted | DraftPreserved


def submit_or_preserve(
    monitor: CrmHealthMonitor,
    store: DraftStore,
    key: DraftKey,
    content: Mapping[str, Any],
    submit: Callable[[], Any],
    *,
    now: datetime | None = None,
) -> SubmitOutcome:
    """Hand work to the CRM, or keep it safe locally (crm_draft_preservation).

    An open breaker preserves immediately — no timeout paid on work the
    monitor already knows cannot land. A live attempt that raises
    :class:`CrmUnavailableError` records the failure and preserves. A
    successful submit discards the key's draft (the work has landed; a
    leftover draft would resurrect it). EVERY other submit failure —
    validation, refusal, terminal write faults — propagates untouched:
    classifying and retrying those is the WTK-152 write contract, not this
    process.
    """
    reason: str | None = None
    if monitor.allow_attempt(now=now):
        try:
            result = submit()
        except CrmUnavailableError as exc:
            reason = str(exc) or "CRM did not answer"
            monitor.record_failure(reason, now=now)
        else:
            monitor.record_success()
            discard_draft(store, key)
            return SubmitAccepted(result=result)
    state = monitor.availability()
    reason = reason or state.reason or "The CRM is not answering."
    draft = preserve_draft(store, key, content, reason=reason, now=now)
    return DraftPreserved(draft=draft, unavailable_since=state.unavailable_since, reason=reason)
