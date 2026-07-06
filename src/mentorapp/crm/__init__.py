"""CRM integration engine: the authentication seams and the EspoCRM binding.

One canonical home for how the app authenticates against, and acts upon, the
CRM system of record. Seams (CRM-agnostic protocols and outcomes) live in
:mod:`mentorapp.crm.auth`; the EspoCRM plug lives in :mod:`mentorapp.crm.espo`;
the production HTTP transport and env wiring live in :mod:`mentorapp.crm.http`;
the master-record write-through and write-retry semantics (WTK-152) live in
:mod:`mentorapp.crm.write_through`.
"""

from mentorapp.crm.auth import (
    CredentialsRejectedError,
    CredentialVerification,
    CrmAccess,
    CrmAuthError,
    CrmCredentialExpiredError,
    CrmUnavailableError,
    CrmUserCredential,
    CrmVerifiedIdentity,
    ForgotPassword,
)
from mentorapp.crm.espo import (
    EspoAuthGateway,
    EspoOperationRejectedError,
    EspoResponse,
    EspoTransport,
)
from mentorapp.crm.http import HttpxEspoTransport, espo_gateway_from_env
from mentorapp.crm.write_through import (
    CRM_WRITE_RETRY_JOB_TYPE,
    CrmWrite,
    CrmWriteThrough,
    WriteFaultDisposition,
    classify_crm_write_fault,
    retry_job_payload,
    write_from_retry_payload,
)

__all__ = [
    "CRM_WRITE_RETRY_JOB_TYPE",
    "CredentialVerification",
    "CredentialsRejectedError",
    "CrmAccess",
    "CrmAuthError",
    "CrmCredentialExpiredError",
    "CrmUnavailableError",
    "CrmUserCredential",
    "CrmVerifiedIdentity",
    "CrmWrite",
    "CrmWriteThrough",
    "EspoAuthGateway",
    "EspoOperationRejectedError",
    "EspoResponse",
    "EspoTransport",
    "ForgotPassword",
    "HttpxEspoTransport",
    "WriteFaultDisposition",
    "classify_crm_write_fault",
    "espo_gateway_from_env",
    "retry_job_payload",
    "write_from_retry_payload",
]
