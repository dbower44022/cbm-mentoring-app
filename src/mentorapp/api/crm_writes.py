"""CRM write-through and write-retry, implemented (WTK-157, REQ-062/REQ-064).

The WTK-152 design (:mod:`mentorapp.crm.write_through`) executed as the two
processes it promised, composing the one queue instead of growing a second
retry engine:

- ``crm_write_through`` — the request-time write-back: one synchronous
  attempt AS THE USER through the :class:`~mentorapp.crm.write_through.CrmWriteThrough`
  seam, then exactly one fork per
  :func:`~mentorapp.crm.write_through.classify_crm_write_fault`. Success is
  one CRM write; a transient fault preserves the accepted intent as a
  ``crmWriteRetry`` job and answers with its identifier; a terminal fault
  surfaces immediately with the CRM's specific cause. The three outcomes are
  distinct types (the ``FreshRead``/``StaleRead`` pattern) so an endpoint
  cannot drop the fork on the floor.
- ``crm_write_retry_job`` — the queue handler for deferred writes: the worker
  (:mod:`mentorapp.automation.worker`) owns backoff, lease reclaim, and
  parking; this handler owns only re-applying the preserved write under the
  integration credential and re-speaking the same fault fork — a transient
  fault raises for backoff, a terminal one parks as ``needsAttention``.
  Replays are duplicate-safe by the updates-only contract: the same absolute
  values land on the same record and converge (REQ-064).

Enqueued jobs carry ``acting_user_id`` as ``createdBy``, so the worker's
terminal transitions ring the requesting user's bell (REQ-014) and the app's
own audit trail keeps human attribution even though the deferred attempt runs
under the integration account.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session

from mentorapp.automation.worker import JobHandler, PermanentJobError, enqueue_job
from mentorapp.crm.auth import CrmUserCredential
from mentorapp.crm.espo import EspoOperationRejectedError
from mentorapp.crm.write_through import (
    CRM_WRITE_RETRY_JOB_TYPE,
    CrmWrite,
    CrmWriteThrough,
    classify_crm_write_fault,
    retry_job_payload,
    write_from_retry_payload,
)
from mentorapp.observability import get_logger
from mentorapp.storage import BackgroundJob

logger = get_logger(__name__)


@dataclass(frozen=True)
class WriteApplied:
    """The CRM took the write in the user's own request: exactly one CRM write."""


@dataclass(frozen=True)
class WriteDeferred:
    """The CRM could not answer now; the accepted intent rides ``crmWriteRetry``.

    ``retry_job_id`` is what the request answers with in ``meta`` and what
    the transient :func:`~mentorapp.ui.outage_recovery.write_failure_notice`
    requires; completion surfaces through the change feed like every job.
    """

    retry_job_id: uuid.UUID
    reason: str


@dataclass(frozen=True)
class WriteRefused:
    """The CRM answered and refused: terminal, never enqueued (REQ-064).

    ``crm_cause`` is the CRM's own words for the envelope's structured
    errors; ``fault`` rides along for the error-mapping layer.
    """

    crm_cause: str
    fault: Exception


CrmWriteOutcome = WriteApplied | WriteDeferred | WriteRefused


def crm_fault_cause(fault: Exception) -> str:
    """The CRM's specific cause for a failed write, for humans (REQ-064).

    An Espo refusal carries its own message in the rejection payload — that
    is what the user acts on; any other fault speaks through its ``str``.
    """
    if isinstance(fault, EspoOperationRejectedError) and isinstance(fault.payload, Mapping):
        message = fault.payload.get("message")
        if message:
            return str(message)
    return str(fault) or fault.__class__.__name__


def crm_write_through(
    session: Session,
    write_through: CrmWriteThrough,
    credential: CrmUserCredential,
    write: CrmWrite,
) -> CrmWriteOutcome:
    """Apply one accepted master-record change to the CRM (REQ-062).

    One synchronous attempt under the user's own ``credential`` (the CRM's
    audit trail attributes the change to the human), then the one fork:
    returns :class:`WriteApplied` on success, :class:`WriteDeferred` after
    enqueueing the ``crmWriteRetry`` job on a transient fault, and
    :class:`WriteRefused` with the CRM's cause on a terminal one. Never
    raises the CRM fault itself — the outcome IS the answer.
    """
    context = {
        "crmWriteID": str(write.crm_write_id),
        "crmEntityType": write.crm_entity_type,
        "crmRecordID": write.crm_record_id,
    }
    try:
        write_through.apply(credential, write)
    except Exception as fault:
        if classify_crm_write_fault(fault) == "transient":
            job = enqueue_job(
                session,
                CRM_WRITE_RETRY_JOB_TYPE,
                retry_job_payload(write),
                acting_user_id=write.acting_user_id,
            )
            reason = str(fault) or "The CRM could not take the write right now."
            logger.warning(
                "CRM write deferred to the retry queue",
                extra={"context": {**context, "retryJobID": str(job.job_id)}},
            )
            return WriteDeferred(retry_job_id=job.job_id, reason=reason)
        cause = crm_fault_cause(fault)
        logger.warning(
            "CRM refused a master-record write",
            extra={"context": {**context, "crmCause": cause}},
        )
        return WriteRefused(crm_cause=cause, fault=fault)
    logger.info("master-record write applied to the CRM", extra={"context": context})
    return WriteApplied()


def crm_write_retry_job(
    write_through: CrmWriteThrough, integration_credential: CrmUserCredential
) -> JobHandler:
    """The queue handler for :data:`CRM_WRITE_RETRY_JOB_TYPE` (REQ-064).

    Re-applies the preserved write under the integration credential (the
    user's token is session-scoped and never persisted — WTK-003). Transient
    faults raise for the worker's backoff; terminal faults — including a
    malformed payload, which no retry can repair — raise
    :class:`PermanentJobError` and park for a human.
    """

    def handle(session: Session, job: BackgroundJob) -> None:
        try:
            write = write_from_retry_payload(job.job_payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise PermanentJobError(f"malformed crmWriteRetry payload: {exc}") from exc
        try:
            write_through.apply(integration_credential, write)
        except Exception as fault:
            if classify_crm_write_fault(fault) == "transient":
                raise
            raise PermanentJobError(crm_fault_cause(fault)) from fault
        logger.info(
            "deferred CRM write landed",
            extra={
                "context": {
                    "crmWriteID": str(write.crm_write_id),
                    "crmEntityType": write.crm_entity_type,
                    "crmRecordID": write.crm_record_id,
                    "jobID": str(job.job_id),
                }
            },
        )
        return None

    return handle


def integration_credential_from_env() -> CrmUserCredential:
    """Build the deferred-retry integration credential from the environment.

    ``MENTORAPP_ESPO_INTEGRATION_USERNAME`` / ``MENTORAPP_ESPO_INTEGRATION_TOKEN``
    name the app's dedicated integration account — the one deliberate
    deviation from user-as-user execution (WTK-152). Fail-loud like
    ``espo_gateway_from_env``: a retry path silently running as nobody is a
    worse failure mode than a clear startup error.
    """
    username = os.environ.get("MENTORAPP_ESPO_INTEGRATION_USERNAME")
    token = os.environ.get("MENTORAPP_ESPO_INTEGRATION_TOKEN")
    if not username or not token:
        raise RuntimeError(
            "MENTORAPP_ESPO_INTEGRATION_USERNAME/MENTORAPP_ESPO_INTEGRATION_TOKEN "
            "are not set; the crmWriteRetry handler cannot be constructed."
        )
    return CrmUserCredential(username=username, secret=token)
