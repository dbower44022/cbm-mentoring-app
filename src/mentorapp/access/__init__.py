"""Access layer: who may see and do what, decided server-side (PI-001).

Three processes over the storage primitives, one module each, plus the
identity bridge they all stand on:

- :mod:`~mentorapp.access.identity` — the one seam where a CRM-verified
  identity becomes the app-side :class:`VerifiedIdentity` (find-or-provision
  the ``appUser``, map CRM roles to grant vocabulary, carry the credential).
- :mod:`~mentorapp.access.grants` — DataSourceAccessControl (REQ-006):
  per-source role grants as the approval boundary, composed with the
  admin-SQL executor's server-bound user row filter.
- :mod:`~mentorapp.access.sessions` — SessionManagement (REQ-005):
  server-side sessions behind an opaque browser reference, with in-place
  re-authentication, the dirty-window guard, and cross-window logout.
- :mod:`~mentorapp.access.tokens` — TokenAction (REQ-007): signed expiring
  action links, use-accounted server-side, with a full mint/redeem/revoke
  audit trail.
- :mod:`~mentorapp.access.verification` — the wired login seams (REQ-008):
  CRM credential verification composed with the identity bridge into the
  auth endpoints' verifier, and the CRM-connected forgot-password flow.

Persistence of grant/session/token records is the storage layer's design
(WTK-001); these processes speak to it through the narrow store protocols
defined alongside each process, with in-memory reference implementations
carrying the design-gate tests. Grants have their stored implementation
here too (WTK-007): :class:`StoredGrantRegistry` over the WTK-001 rows,
with :func:`run_stored_data_source` as the API-facing entry point.
"""

from mentorapp.access.credentials import CredentialCipher, CredentialSealError
from mentorapp.access.grants import (
    DataSourceAccessError,
    DataSourceNotFoundError,
    InMemoryGrantRegistry,
    SourceGrant,
    StoredGrantRegistry,
    authorize_data_source,
    grant_data_source_role,
    load_stored_source,
    revoke_data_source_role,
    run_data_source,
    run_stored_data_source,
)
from mentorapp.access.identity import IdentityBridge, StoredIdentityBridge, VerifiedIdentity
from mentorapp.access.sessions import (
    IdentityMismatchError,
    InMemorySessionStore,
    ReauthRequiredError,
    SessionEndedError,
    SessionManagement,
    SessionNotFoundError,
    SessionRecord,
    SessionState,
    SessionStore,
    StoredSessionStore,
)
from mentorapp.access.tokens import (
    InMemoryTokenActionStore,
    StoredTokenActionStore,
    TokenActionError,
    TokenActionRecord,
    TokenActionService,
    TokenAuditEvent,
    TokenExhaustedError,
    TokenExpiredError,
    TokenInvalidError,
    TokenRevokedError,
)
from mentorapp.access.verification import CrmCredentialVerifier, CrmForgotPasswordFlow

__all__ = [
    "CredentialCipher",
    "CredentialSealError",
    "CrmCredentialVerifier",
    "CrmForgotPasswordFlow",
    "DataSourceAccessError",
    "DataSourceNotFoundError",
    "IdentityBridge",
    "IdentityMismatchError",
    "InMemoryGrantRegistry",
    "InMemorySessionStore",
    "InMemoryTokenActionStore",
    "ReauthRequiredError",
    "SessionEndedError",
    "SessionManagement",
    "SessionNotFoundError",
    "SessionRecord",
    "SessionState",
    "SessionStore",
    "SourceGrant",
    "StoredGrantRegistry",
    "StoredIdentityBridge",
    "StoredSessionStore",
    "StoredTokenActionStore",
    "TokenActionError",
    "TokenActionRecord",
    "TokenActionService",
    "TokenAuditEvent",
    "TokenExhaustedError",
    "TokenExpiredError",
    "TokenInvalidError",
    "TokenRevokedError",
    "VerifiedIdentity",
    "authorize_data_source",
    "grant_data_source_role",
    "load_stored_source",
    "revoke_data_source_role",
    "run_data_source",
    "run_stored_data_source",
]
