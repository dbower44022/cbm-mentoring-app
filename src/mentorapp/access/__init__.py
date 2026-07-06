"""Access layer: who may see and do what, decided server-side (PI-001).

Three processes over the storage primitives, one module each, plus the
identity bridge they all stand on:

- :mod:`~mentorapp.access.identity` — the one seam where a CRM-verified
  identity becomes the app-side :class:`VerifiedIdentity` (find-or-provision
  the ``appUser``, map CRM roles to grant vocabulary, carry the credential).
- :mod:`~mentorapp.access.grants` — DataSourceAccessControl (REQ-006):
  per-source role grants as the approval boundary, composed with the
  admin-SQL executor's server-bound user row filter.
- :mod:`~mentorapp.access.areas` — UserAreaAccess (WTK-025, REQ-003/REQ-015):
  which Areas a user may enter, derived from the same grant boundary — the
  ``accessible_panel_keys`` input the Home rail and the startup/deep-link
  fallbacks consume; never a second per-user assignment table.
- :mod:`~mentorapp.access.lookup_grants` — LookupDataAccess (WTK-061,
  REQ-036): the grant model for relationship type-ahead search — a lookup
  over a related entity is governed by that entity's bound data source at
  the same REQ-006 boundary, with :func:`stored_lookup_scope` returning
  authorization and the user-row scope in one call.
- :mod:`~mentorapp.access.views` — ViewAndSourcePermissions (WTK-044,
  REQ-017/REQ-019/REQ-028): the view lifecycle rules (system views
  read-only, own-view management, admin promotion), the persona →
  capability map over the ``accessGrant`` rows, and the standard
  ``permission_refusal`` state a denied deep link renders.
- :mod:`~mentorapp.access.view_enforcement` — StoredViewEnforcement
  (WTK-050): the WTK-044 rules bound to the persisted ``gridView`` rows —
  load-and-authorize entry points for view management and promotion, the
  stored REQ-019 authoring gate, and the ``gridDeepLink`` fact assembly the
  API's pure resolver consumes (REQ-028).
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

from mentorapp.access.areas import (
    AreaDescriptor,
    accessible_area_keys,
    authorize_area,
    is_area_accessible,
)
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
    roles_cover_data_source,
    run_data_source,
    run_stored_data_source,
)
from mentorapp.access.identity import IdentityBridge, StoredIdentityBridge, VerifiedIdentity
from mentorapp.access.lookup_grants import (
    InMemoryLookupSources,
    LookupBinding,
    LookupScope,
    LookupSourceResolver,
    LookupUnboundError,
    authorize_lookup_search,
    is_lookup_searchable,
    stored_lookup_scope,
)
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
from mentorapp.access.view_enforcement import (
    DeepLinkNotFoundError,
    GridOnlyLinkFacts,
    NamedViewLinkFacts,
    ViewNotFoundError,
    authorize_stored_data_source_authoring,
    authorize_stored_view_management,
    authorize_stored_view_promotion,
    load_deep_link_facts,
    load_live_view,
    stored_save_disposition,
    stored_view_visible_to,
    view_facts,
)
from mentorapp.access.views import (
    ADMIN_CAPABILITIES,
    CAP_DATA_SOURCE_AUTHOR,
    CAP_LINK_SHARE,
    CAP_VIEW_APPLY_TEMPORARY,
    CAP_VIEW_MANAGE_OWN,
    CAP_VIEW_PROMOTE,
    CAP_VIEW_SAVE_AS_USER,
    PERMISSION_REFUSAL,
    PERSONA_ADMIN,
    PERSONA_CAPABILITIES,
    PERSONA_USER,
    SAVE_AS_USER,
    SAVE_IN_PLACE,
    USER_BASELINE_CAPABILITIES,
    CapabilityError,
    CapabilityLookup,
    InMemoryCapabilityRegistry,
    StoredCapabilityRegistry,
    ViewFacts,
    ViewPermissionError,
    authorize_capability,
    authorize_data_source_authoring,
    authorize_view_management,
    authorize_view_promotion,
    can_apply_temporarily,
    can_manage_view,
    effective_capabilities,
    holds_capability,
    persona_for,
    save_disposition,
    view_visible_to,
)

__all__ = [
    "ADMIN_CAPABILITIES",
    "CAP_DATA_SOURCE_AUTHOR",
    "CAP_LINK_SHARE",
    "CAP_VIEW_APPLY_TEMPORARY",
    "CAP_VIEW_MANAGE_OWN",
    "CAP_VIEW_PROMOTE",
    "CAP_VIEW_SAVE_AS_USER",
    "PERMISSION_REFUSAL",
    "PERSONA_ADMIN",
    "PERSONA_CAPABILITIES",
    "PERSONA_USER",
    "SAVE_AS_USER",
    "SAVE_IN_PLACE",
    "USER_BASELINE_CAPABILITIES",
    "AreaDescriptor",
    "CapabilityError",
    "CapabilityLookup",
    "CredentialCipher",
    "CredentialSealError",
    "CrmCredentialVerifier",
    "CrmForgotPasswordFlow",
    "DataSourceAccessError",
    "DataSourceNotFoundError",
    "DeepLinkNotFoundError",
    "GridOnlyLinkFacts",
    "IdentityBridge",
    "IdentityMismatchError",
    "InMemoryCapabilityRegistry",
    "InMemoryGrantRegistry",
    "InMemoryLookupSources",
    "InMemorySessionStore",
    "InMemoryTokenActionStore",
    "LookupBinding",
    "LookupScope",
    "LookupSourceResolver",
    "LookupUnboundError",
    "NamedViewLinkFacts",
    "ReauthRequiredError",
    "SessionEndedError",
    "SessionManagement",
    "SessionNotFoundError",
    "SessionRecord",
    "SessionState",
    "SessionStore",
    "SourceGrant",
    "StoredCapabilityRegistry",
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
    "ViewFacts",
    "ViewNotFoundError",
    "ViewPermissionError",
    "accessible_area_keys",
    "authorize_area",
    "authorize_capability",
    "authorize_data_source",
    "authorize_data_source_authoring",
    "authorize_lookup_search",
    "authorize_stored_data_source_authoring",
    "authorize_stored_view_management",
    "authorize_stored_view_promotion",
    "authorize_view_management",
    "authorize_view_promotion",
    "can_apply_temporarily",
    "can_manage_view",
    "effective_capabilities",
    "grant_data_source_role",
    "holds_capability",
    "is_area_accessible",
    "is_lookup_searchable",
    "load_deep_link_facts",
    "load_live_view",
    "load_stored_source",
    "persona_for",
    "revoke_data_source_role",
    "roles_cover_data_source",
    "run_data_source",
    "run_stored_data_source",
    "save_disposition",
    "stored_lookup_scope",
    "stored_save_disposition",
    "stored_view_visible_to",
    "view_facts",
    "view_visible_to",
]
