"""CRM integration engine: the authentication seams and the EspoCRM binding.

One canonical home for how the app authenticates against, and acts upon, the
CRM system of record. Seams (CRM-agnostic protocols and outcomes) live in
:mod:`mentorapp.crm.auth`; the EspoCRM plug lives in :mod:`mentorapp.crm.espo`.
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

__all__ = [
    "CredentialVerification",
    "CredentialsRejectedError",
    "CrmAccess",
    "CrmAuthError",
    "CrmCredentialExpiredError",
    "CrmUnavailableError",
    "CrmUserCredential",
    "CrmVerifiedIdentity",
    "EspoAuthGateway",
    "EspoOperationRejectedError",
    "EspoResponse",
    "EspoTransport",
    "ForgotPassword",
]
