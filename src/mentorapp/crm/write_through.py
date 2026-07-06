"""CRM write-through and write-retry semantics (WTK-152, REQ-062/REQ-064).

The design for how mentor/client/engagement master-record changes leave the
app and land in the CRM system of record, decided once for every write path:

- **The CRM write is part of the user's request, never an eventual sync**
  (REQ-062). The app holds no master tables to fork; a master-record edit the
  API accepts is applied to the CRM through the :class:`CrmWriteThrough` seam
  before the request answers. The first attempt runs synchronously AS THE
  USER over :class:`~mentorapp.crm.auth.CrmAccess` (WTK-003), so the CRM's
  own audit trail attributes the change to the human who made it. Success
  means exactly one CRM write and the queue is never involved.
- **One classification point forks every fault** (REQ-064).
  :func:`classify_crm_write_fault` is the ONE place the transient-vs-terminal
  call is made; endpoint handlers and the retry job handler both consume its
  answer and never re-derive it:

  - *transient* — the CRM could not answer
    (:class:`~mentorapp.crm.auth.CrmUnavailableError`), or answered "not
    now" (HTTP 408/429 through
    :class:`~mentorapp.crm.espo.EspoOperationRejectedError`). The accepted
    intent is preserved as a ``crmWriteRetry`` background job (DB-S11): the
    worker in :mod:`mentorapp.automation.worker` already owns backoff,
    lease-reclaim, and parking — this module contributes only the payload
    codec and the disposition, never a second retry engine. The request
    answers with the job identifier in ``meta``; completion surfaces through
    the change feed like every job (REQ-058).
  - *terminal* — the CRM answered and refused
    (:class:`~mentorapp.crm.espo.EspoOperationRejectedError`), or the
    credential no longer stands
    (:class:`~mentorapp.crm.auth.CrmCredentialExpiredError`). Surfaced
    immediately in the same request with the CRM's specific cause in the
    envelope's structured errors; the retry affordance is deliberate
    resubmission of the same PATCH. Never enqueued — retrying cannot fix a
    refusal. Faults this module cannot prove transient default to terminal:
    surfacing an unknown fault beats silently spinning on it.

- **Idempotent completion without duplicates** (REQ-064). v1 write-through
  scope is UPDATES to existing CRM records only — mentor/client/engagement
  master records are created by the existing intake and staff tools (domain
  brief), so the app addresses records it already references by
  ``crm_record_id``. An update replay re-sends absolute field values to the
  same record: it converges and cannot mint a duplicate, which is why
  :class:`CrmWrite` refuses to exist without a target record id. Field-level
  last-write-wins on replay is the deliberate boring call for a
  one-volunteer-ops system; each write also carries ``crm_write_id`` (UUIDv7,
  minted once at intent time, identical across every retry) so log lines,
  the job payload, and any future create capability share one correlation
  key — a create capability plugs in later by adding a stamped-key lookup to
  the seam, not by changing this contract.
- **Deferred retries run under the integration credential, recorded.** The
  user's CRM token is session-scoped and never persisted (WTK-003), so a
  retry firing after backoff cannot run as the user. This is the one
  deliberate deviation from user-as-user execution: the retry job handler
  executes under the app's dedicated integration account
  (env-wired like ``espo_gateway_from_env``), and the payload's
  ``acting_user_id`` keeps the human attribution in the app's own audit
  trail and structured logs. A credential failure under the integration
  account is broken configuration, not weather — it parks for a human.

The seam is a ``Protocol`` (the ``FeedPushTransport`` pattern):
:class:`EspoWriteThrough` (WTK-157) is the EspoCRM plug, generic over
:class:`~mentorapp.crm.auth.CrmAccess` so it inherits the gateway's fault
vocabulary instead of re-deriving HTTP policy; tests and local runs plug
fakes. The request-time fork and the retry job handler live in
:mod:`mentorapp.api.crm_writes`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from mentorapp.crm.auth import CrmAccess, CrmUnavailableError, CrmUserCredential
from mentorapp.crm.espo import EspoOperationRejectedError
from mentorapp.storage.ids import uuid7

# The job-type vocabulary entry for deferred CRM writes; the worker's handler
# registry and enqueue sites must share this one spelling.
CRM_WRITE_RETRY_JOB_TYPE = "crmWriteRetry"

# Espo maps throttling and request-timeout answers into the generic 4xx
# rejection; they are "not now", not "no" — the only 4xx statuses that
# classify as transient.
_TRANSIENT_REJECTION_STATUSES = frozenset({408, 429})

WriteFaultDisposition = Literal["transient", "terminal"]


@dataclass(frozen=True)
class CrmWrite:
    """One accepted master-record change bound for the CRM.

    ``crm_entity_type`` and ``crm_record_id`` speak the CRM's own vocabulary
    (the Espo entity name and record id the app references, REQ-062) —
    this is the CRM side of the join, not an app-side key.
    ``changed_fields`` carries only what the user changed, with absolute
    values, mirroring the PATCH contract (DB-S12): replays converge instead
    of duplicating. ``crm_write_id`` is minted once when the intent is
    accepted and never re-minted on retry — the correlation key across the
    request, the job payload, and the structured log. ``acting_user_id``
    preserves human attribution when a deferred retry cannot run as the user.
    """

    crm_entity_type: str
    crm_record_id: str
    changed_fields: Mapping[str, Any]
    acting_user_id: uuid.UUID
    crm_write_id: uuid.UUID = field(default_factory=uuid7)

    def __post_init__(self) -> None:
        # Updates-only is the duplicate-safety contract itself: a write with
        # no target record would be a create, which this design defers.
        if not self.crm_record_id:
            raise ValueError("CrmWrite requires an existing crm_record_id (updates only)")
        if not self.changed_fields:
            raise ValueError("CrmWrite carries at least one changed field")


class CrmWriteThrough(Protocol):
    """The pluggable write seam: one accepted change → one CRM update.

    ``apply`` performs the write under ``credential`` — the user's own
    session credential on the synchronous first attempt, the integration
    account's on a deferred retry. It returns only on success; it raises the
    :mod:`mentorapp.crm.auth` / :mod:`mentorapp.crm.espo` fault vocabulary
    for :func:`classify_crm_write_fault` to fork.
    """

    def apply(self, credential: CrmUserCredential, write: CrmWrite) -> None: ...


@dataclass(frozen=True)
class EspoWriteThrough:
    """The EspoCRM plug of :class:`CrmWriteThrough` (WTK-157, REQ-062).

    ``apply`` maps one :class:`CrmWrite` onto ``PUT {entityType}/{recordID}``
    over :class:`~mentorapp.crm.auth.CrmAccess`. It returns only on success
    and lets the access seam's fault vocabulary propagate untouched for
    :func:`classify_crm_write_fault` — no fault judgement is made here.
    """

    access: CrmAccess

    def apply(self, credential: CrmUserCredential, write: CrmWrite) -> None:
        # Espo's PUT applies only the attributes present in the body (its
        # partial-update semantics), which is exactly the PATCH-shaped
        # changed-fields contract: a replay re-sends the same absolute values
        # to the same record and converges instead of duplicating (REQ-064).
        self.access.execute(
            credential,
            "PUT",
            f"{write.crm_entity_type}/{write.crm_record_id}",
            json=dict(write.changed_fields),
        )


def classify_crm_write_fault(fault: Exception) -> WriteFaultDisposition:
    """The one transient-vs-terminal call for a failed CRM write (REQ-064).

    Transient means the CRM could not answer or asked to come back later;
    everything else — refusals, dropped credentials, faults this module
    cannot prove transient — is terminal and surfaces immediately with its
    cause. Callers map transient onto the worker's retry path and terminal
    onto the envelope's structured errors / ``PermanentJobError``.
    """
    if isinstance(fault, CrmUnavailableError):
        return "transient"
    if (
        isinstance(fault, EspoOperationRejectedError)
        and fault.status_code in _TRANSIENT_REJECTION_STATUSES
    ):
        return "transient"
    return "terminal"


def retry_job_payload(write: CrmWrite) -> dict[str, Any]:
    """Encode one accepted write as the ``crmWriteRetry`` job payload.

    Keys speak the payload vocabulary (camelCase, like every JSONB document);
    values are JSON-safe so the payload survives the ``backgroundJob`` row.
    """
    return {
        "crmWriteID": str(write.crm_write_id),
        "crmEntityType": write.crm_entity_type,
        "crmRecordID": write.crm_record_id,
        "changedFields": dict(write.changed_fields),
        "actingUserID": str(write.acting_user_id),
    }


def write_from_retry_payload(payload: Mapping[str, Any]) -> CrmWrite:
    """Decode a ``crmWriteRetry`` job payload back into the write it preserves.

    Raises ``KeyError``/``ValueError`` on a malformed payload — inside the
    worker that classifies as :class:`~mentorapp.automation.worker.PermanentJobError`
    territory (retrying cannot repair a payload), never a silent skip.
    """
    return CrmWrite(
        crm_entity_type=str(payload["crmEntityType"]),
        crm_record_id=str(payload["crmRecordID"]),
        changed_fields=dict(payload["changedFields"]),
        acting_user_id=uuid.UUID(str(payload["actingUserID"])),
        crm_write_id=uuid.UUID(str(payload["crmWriteID"])),
    )
