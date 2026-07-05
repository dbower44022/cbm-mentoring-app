"""CRM integration engine: the authentication seams and the EspoCRM binding.

One canonical home for how the app authenticates against, and acts upon, the
CRM system of record. Seams (CRM-agnostic protocols and outcomes) live in
:mod:`mentorapp.crm.auth`; the EspoCRM plug lives in :mod:`mentorapp.crm.espo`;
the production HTTP transport and env wiring live in :mod:`mentorapp.crm.http`.
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
    "HttpxEspoTransport",
    "espo_gateway_from_env",
]
