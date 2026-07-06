"""The CRM write-through and write-retry design contract (WTK-152, REQ-062/064).

Covers the seam-level guarantees the design promises: the one
transient-vs-terminal classification point, the updates-only duplicate-safety
rule on :class:`CrmWrite`, the stable idempotency key, and the lossless
JSON-safe round trip of the ``crmWriteRetry`` job payload.
"""

from __future__ import annotations

import json
import uuid

import pytest

from mentorapp.crm import (
    CRM_WRITE_RETRY_JOB_TYPE,
    CredentialsRejectedError,
    CrmCredentialExpiredError,
    CrmUnavailableError,
    CrmWrite,
    EspoOperationRejectedError,
    classify_crm_write_fault,
    retry_job_payload,
    write_from_retry_payload,
)
from mentorapp.storage.ids import uuid7


def _write(**overrides: object) -> CrmWrite:
    values: dict = {
        "crm_entity_type": "Contact",
        "crm_record_id": "espo-5f2a",
        "changed_fields": {"phoneNumber": "+15555550100"},
        "acting_user_id": uuid7(),
    }
    values.update(overrides)
    return CrmWrite(**values)


class TestFaultClassification:
    def test_crm_unavailable_is_transient(self) -> None:
        assert classify_crm_write_fault(CrmUnavailableError("no answer")) == "transient"

    @pytest.mark.parametrize("status_code", [408, 429])
    def test_come_back_later_rejections_are_transient(self, status_code: int) -> None:
        fault = EspoOperationRejectedError(status_code, {"message": "throttled"})
        assert classify_crm_write_fault(fault) == "transient"

    @pytest.mark.parametrize("status_code", [400, 403, 404, 409])
    def test_refusals_are_terminal(self, status_code: int) -> None:
        fault = EspoOperationRejectedError(status_code, {"message": "refused"})
        assert classify_crm_write_fault(fault) == "terminal"

    def test_dropped_credential_is_terminal(self) -> None:
        # Synchronously: re-establish the login; under the integration
        # account: broken configuration. Neither is fixed by backoff.
        assert classify_crm_write_fault(CrmCredentialExpiredError("dropped")) == "terminal"

    def test_rejected_credentials_are_terminal(self) -> None:
        assert classify_crm_write_fault(CredentialsRejectedError("no")) == "terminal"

    def test_unknown_faults_default_to_terminal(self) -> None:
        # Anything the design cannot prove transient must surface, not spin.
        assert classify_crm_write_fault(ValueError("unforeseen")) == "terminal"


class TestCrmWriteContract:
    def test_updates_only_a_write_without_a_target_record_is_refused(self) -> None:
        with pytest.raises(ValueError, match="crm_record_id"):
            _write(crm_record_id="")

    def test_an_empty_change_set_is_refused(self) -> None:
        with pytest.raises(ValueError, match="changed field"):
            _write(changed_fields={})

    def test_write_id_is_minted_once_at_intent_time(self) -> None:
        write = _write()
        assert isinstance(write.crm_write_id, uuid.UUID)
        assert write.crm_write_id.version == 7


class TestRetryPayloadCodec:
    def test_round_trip_preserves_the_write_and_its_identity(self) -> None:
        write = _write(changed_fields={"phoneNumber": "+15555550100", "cityName": "Waco"})
        decoded = write_from_retry_payload(retry_job_payload(write))
        assert decoded == write
        # The idempotency key survives the queue: a replay is the SAME write.
        assert decoded.crm_write_id == write.crm_write_id
        assert decoded.acting_user_id == write.acting_user_id

    def test_payload_is_json_safe_for_the_job_row(self) -> None:
        payload = retry_job_payload(_write())
        assert json.loads(json.dumps(payload)) == payload

    def test_malformed_payload_raises_instead_of_skipping(self) -> None:
        with pytest.raises(KeyError):
            write_from_retry_payload({"crmEntityType": "Contact"})

    def test_job_type_speaks_the_vocabulary(self) -> None:
        assert CRM_WRITE_RETRY_JOB_TYPE == "crmWriteRetry"
