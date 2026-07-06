"""CRM write-through and write-retry, implemented (WTK-157, REQ-062/REQ-064).

Exercises the request-time fork (applied / deferred / refused), the Espo
binding of the write seam, the ``crmWriteRetry`` handler on the real worker
(backoff re-queue, ``needsAttention`` parking, integration-credential
execution), and the duplicate-safe replay guarantee.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.api.crm_writes import (
    WriteApplied,
    WriteDeferred,
    WriteRefused,
    crm_fault_cause,
    crm_write_retry_job,
    crm_write_through,
    integration_credential_from_env,
)
from mentorapp.automation.worker import process_next_job, retry_backoff
from mentorapp.crm import (
    CRM_WRITE_RETRY_JOB_TYPE,
    CrmUnavailableError,
    CrmUserCredential,
    CrmWrite,
    EspoOperationRejectedError,
    EspoWriteThrough,
    retry_job_payload,
    write_from_retry_payload,
)
from mentorapp.storage import AppUser, BackgroundJob, utcnow
from mentorapp.storage.ids import uuid7

USER_CREDENTIAL = CrmUserCredential(username="dwatson", secret="user-token")
INTEGRATION_CREDENTIAL = CrmUserCredential(username="mentorapp-bot", secret="bot-token")


def _acting_user(session: Session) -> uuid.UUID:
    # The worker's terminal-transition bell (REQ-014) FK-references appUser,
    # so worker-driven tests need the acting user to really exist.
    user = AppUser(crm_user_id=f"crm-{uuid7()}", username=f"mentor-{uuid7()}")
    session.add(user)
    session.flush()
    return user.user_id


def _write(**overrides: Any) -> CrmWrite:
    values: dict[str, Any] = {
        "crm_entity_type": "Contact",
        "crm_record_id": "espo-5f2a",
        "changed_fields": {"phoneNumber": "+15555550100"},
        "acting_user_id": uuid7(),
    }
    values.update(overrides)
    return CrmWrite(**values)


@dataclass
class FakeAccess:
    """A recording :class:`CrmAccess`: scripted faults first, then success."""

    faults: list[Exception] = dataclass_field(default_factory=list)
    calls: list[tuple[CrmUserCredential, str, str, dict[str, Any] | None]] = dataclass_field(
        default_factory=list
    )

    def execute(
        self,
        credential: CrmUserCredential,
        method: str,
        path: str,
        *,
        params: Any = None,
        json: Any = None,
    ) -> Any:
        self.calls.append((credential, method, path, json))
        if self.faults:
            raise self.faults.pop(0)
        return {"id": path.rpartition("/")[2]}


def _queued_jobs(session: Session) -> list[BackgroundJob]:
    return list(session.scalars(select(BackgroundJob)))


class TestEspoWriteThrough:
    def test_apply_is_a_partial_put_to_the_referenced_record(self) -> None:
        access = FakeAccess()
        write = _write(changed_fields={"phoneNumber": "+15555550100", "cityName": "Waco"})
        EspoWriteThrough(access).apply(USER_CREDENTIAL, write)
        assert access.calls == [
            (
                USER_CREDENTIAL,
                "PUT",
                "Contact/espo-5f2a",
                {"phoneNumber": "+15555550100", "cityName": "Waco"},
            )
        ]

    def test_faults_propagate_unjudged(self) -> None:
        access = FakeAccess(faults=[EspoOperationRejectedError(403, {"message": "forbidden"})])
        with pytest.raises(EspoOperationRejectedError):
            EspoWriteThrough(access).apply(USER_CREDENTIAL, _write())


class TestCrmWriteThrough:
    def test_success_is_one_crm_write_and_no_job(self, session: Session) -> None:
        access = FakeAccess()
        outcome = crm_write_through(
            session, EspoWriteThrough(access), USER_CREDENTIAL, _write()
        )
        assert outcome == WriteApplied()
        assert len(access.calls) == 1
        # The first attempt runs AS THE USER (REQ-062): CRM audit attribution.
        assert access.calls[0][0] == USER_CREDENTIAL
        assert _queued_jobs(session) == []

    def test_transient_fault_defers_the_accepted_intent(self, session: Session) -> None:
        access = FakeAccess(faults=[CrmUnavailableError("no answer")])
        write = _write()
        outcome = crm_write_through(session, EspoWriteThrough(access), USER_CREDENTIAL, write)
        assert isinstance(outcome, WriteDeferred)
        assert outcome.reason == "no answer"
        (job,) = _queued_jobs(session)
        assert job.job_id == outcome.retry_job_id
        assert job.job_type == CRM_WRITE_RETRY_JOB_TYPE
        assert job.job_status == "pending"
        # The requesting user owns the job (REQ-014 bell) and the payload
        # preserves the write identically — same idempotency key, same values.
        assert job.created_by == write.acting_user_id
        assert write_from_retry_payload(job.job_payload) == write

    def test_come_back_later_rejection_also_defers(self, session: Session) -> None:
        access = FakeAccess(faults=[EspoOperationRejectedError(429, {"message": "throttled"})])
        outcome = crm_write_through(
            session, EspoWriteThrough(access), USER_CREDENTIAL, _write()
        )
        assert isinstance(outcome, WriteDeferred)

    def test_refusal_surfaces_the_crm_cause_and_never_enqueues(self, session: Session) -> None:
        fault = EspoOperationRejectedError(400, {"message": "Phone number is not valid."})
        access = FakeAccess(faults=[fault])
        outcome = crm_write_through(
            session, EspoWriteThrough(access), USER_CREDENTIAL, _write()
        )
        assert outcome == WriteRefused(crm_cause="Phone number is not valid.", fault=fault)
        assert _queued_jobs(session) == []

    def test_unknown_faults_surface_as_terminal(self, session: Session) -> None:
        access = FakeAccess(faults=[ValueError("unforeseen")])
        outcome = crm_write_through(
            session, EspoWriteThrough(access), USER_CREDENTIAL, _write()
        )
        assert isinstance(outcome, WriteRefused)
        assert outcome.crm_cause == "unforeseen"
        assert _queued_jobs(session) == []


class TestCrmFaultCause:
    def test_espo_rejection_speaks_its_own_message(self) -> None:
        fault = EspoOperationRejectedError(409, {"message": "Record is locked."})
        assert crm_fault_cause(fault) == "Record is locked."

    def test_rejection_without_a_message_falls_back_to_str(self) -> None:
        fault = EspoOperationRejectedError(404, None)
        assert crm_fault_cause(fault) == "EspoCRM rejected the operation (HTTP 404)"


def _enqueue_retry(session: Session, write: CrmWrite) -> BackgroundJob:
    access = FakeAccess(faults=[CrmUnavailableError("down")])
    outcome = crm_write_through(session, EspoWriteThrough(access), USER_CREDENTIAL, write)
    assert isinstance(outcome, WriteDeferred)
    (job,) = _queued_jobs(session)
    return job


class TestCrmWriteRetryJob:
    def test_deferred_write_lands_under_the_integration_credential(
        self, session: Session
    ) -> None:
        write = _write(acting_user_id=_acting_user(session))
        job = _enqueue_retry(session, write)
        access = FakeAccess()
        handlers = {
            CRM_WRITE_RETRY_JOB_TYPE: crm_write_retry_job(
                EspoWriteThrough(access), INTEGRATION_CREDENTIAL
            )
        }
        assert process_next_job(session, handlers)
        assert job.job_status == "completed"
        ((credential, method, path, body),) = access.calls
        assert credential == INTEGRATION_CREDENTIAL
        assert (method, path) == ("PUT", "Contact/espo-5f2a")
        assert body == dict(write.changed_fields)

    def test_transient_fault_requeues_with_backoff(self, session: Session) -> None:
        job = _enqueue_retry(session, _write())
        access = FakeAccess(faults=[CrmUnavailableError("still down")])
        handlers = {
            CRM_WRITE_RETRY_JOB_TYPE: crm_write_retry_job(
                EspoWriteThrough(access), INTEGRATION_CREDENTIAL
            )
        }
        now = utcnow()
        assert process_next_job(session, handlers, now=now)
        assert job.job_status == "pending"
        assert job.attempt_count == 1
        assert job.run_after == now + retry_backoff(1)

    def test_terminal_fault_parks_for_attention(self, session: Session) -> None:
        job = _enqueue_retry(session, _write(acting_user_id=_acting_user(session)))
        access = FakeAccess(faults=[EspoOperationRejectedError(403, {"message": "forbidden"})])
        handlers = {
            CRM_WRITE_RETRY_JOB_TYPE: crm_write_retry_job(
                EspoWriteThrough(access), INTEGRATION_CREDENTIAL
            )
        }
        assert process_next_job(session, handlers)
        assert job.job_status == "needsAttention"

    def test_malformed_payload_parks_instead_of_spinning(self, session: Session) -> None:
        from mentorapp.automation.worker import enqueue_job

        job = enqueue_job(session, CRM_WRITE_RETRY_JOB_TYPE, {"crmEntityType": "Contact"})
        handlers = {
            CRM_WRITE_RETRY_JOB_TYPE: crm_write_retry_job(
                EspoWriteThrough(FakeAccess()), INTEGRATION_CREDENTIAL
            )
        }
        assert process_next_job(session, handlers)
        assert job.job_status == "needsAttention"

    def test_replay_converges_on_the_same_record_without_duplicates(
        self, session: Session
    ) -> None:
        # A crash-reclaimed lease re-runs the job (at-least-once): both
        # attempts must be the identical absolute-value update to the SAME
        # record — never a create (REQ-064's no-duplicates guarantee).
        write = _write(acting_user_id=_acting_user(session))
        job = _enqueue_retry(session, write)
        access = FakeAccess(faults=[CrmUnavailableError("mid-flight drop")])
        handlers = {
            CRM_WRITE_RETRY_JOB_TYPE: crm_write_retry_job(
                EspoWriteThrough(access), INTEGRATION_CREDENTIAL
            )
        }
        first = utcnow()
        assert process_next_job(session, handlers, now=first)
        assert process_next_job(session, handlers, now=first + timedelta(hours=2))
        assert job.job_status == "completed"
        assert len(access.calls) == 2
        assert access.calls[0] == access.calls[1]
        replayed = write_from_retry_payload(job.job_payload)
        assert replayed.crm_write_id == write.crm_write_id

    def test_payload_vocabulary_matches_the_wtk152_codec(self, session: Session) -> None:
        write = _write()
        job = _enqueue_retry(session, write)
        assert job.job_payload == retry_job_payload(write)


class TestIntegrationCredentialFromEnv:
    def test_builds_the_credential_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MENTORAPP_ESPO_INTEGRATION_USERNAME", "mentorapp-bot")
        monkeypatch.setenv("MENTORAPP_ESPO_INTEGRATION_TOKEN", "bot-token")
        credential = integration_credential_from_env()
        assert credential == CrmUserCredential(username="mentorapp-bot", secret="bot-token")

    def test_fails_loud_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MENTORAPP_ESPO_INTEGRATION_USERNAME", raising=False)
        monkeypatch.delenv("MENTORAPP_ESPO_INTEGRATION_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="MENTORAPP_ESPO_INTEGRATION_USERNAME"):
            integration_credential_from_env()
