"""API layer — the response-envelope contract, structured errors, and routers.

The contract (REQ-059/DB-S12, REQ-050/DB-S6):

- ``envelope`` is the one response shape every endpoint speaks —
  ``{"data": ..., "meta": ..., "errors": ...}``. No endpoint invents its own.
- ``errors`` defines the structured per-field error entry, the machine-readable
  code vocabulary, and the exceptions + handlers that keep failures inside the
  envelope (all validation failures in one round trip; concurrency conflicts as
  409 with the current record; duplicate creates as 409 with candidates).
- ``routers.schema`` serves ``GET /schema/{entity}`` — the single metadata
  endpoint over the schema registry that drives UI rendering, validation,
  exports, and view columns.
- ``routers.preferences`` serves ``GET/PUT /preferences/{key}`` (REQ-060,
  DB-S13) — the one persistence mechanism for all view/pin/layout/filter
  state, with org-default rows overridden by the caller's own.

The cross-cutting read/write processes (REQ-053/054/055/059, WTK-130) are the
two shared engines every entity endpoint composes — never re-implemented per
feature:

- ``list_engine`` — keyset pagination (cursor codec built once), counts and
  aggregates as a separate whole-set query, and registry-driven server-side
  search (DB-S8).
- ``write_engine`` — create with duplicate detection + recorded overrides,
  field-level PATCH under optimistic concurrency, registry validation of
  built-in and custom fields alike, audit stamping, field history, and
  change-feed append (DB-S4/S5/S12).
- ``records`` — the shared registry/field-map/serialization primitives,
  including the custom-attribute merge into served records (DB-R3).
- ``edit_safety`` — the REQ-013 process design over that write contract:
  concurrent-save conflict resolution (auto-retry vs walk-through, never a
  silent overwrite), the same-user cross-window freshness fan-out (save
  notices ARE change-feed tuples), the dirty-window guard, and the
  edit-collision switch.
- ``crm_writes`` — CRM write-through and write-retry (WTK-157,
  REQ-062/REQ-064): the synchronous master-record write-back as the user
  with its one transient-vs-terminal fork, the ``crmWriteRetry`` queue
  handler running deferred writes under the integration credential, and the
  duplicate-safe replay contract they share.
- ``form_validation`` — the field-settings-driven validation engine design
  (WTK-057, REQ-033): field settings from ``GET /schema/{entity}`` adapted to
  the write engine's ``FieldRule`` so on-exit and save-sweep checks run the
  SAME ``validate_value`` the API runs, the settings-sourced required marker,
  the all-problems save sweep focusing the first problem in display order,
  and inline placement of both client-side and server-returned errors.
- ``form_input`` — the smart input parsing & formatting design (WTK-058,
  REQ-034): focus-exit auto-format for phone/email/website/postal fields
  (a convenience, never a gate), composite-field paste resolution over the
  SAME ``automation.normalization`` parsers that feed the duplicate-match
  shadow columns (confident components fill, the remainder stays visible,
  the paste is never blocked), and the postal → city/state auto-fill read
  that only ever fills empty controls.
- ``grid_surface`` — the grid server API surface design (WTK-042,
  REQ-020/023/026/027/028): the five endpoint contracts with their DB-S11
  over-ten-seconds declarations, whole-filtered-set footer/group aggregates,
  the selection wire shape (explicit vs filteredSet select-all, opaque
  data-source record identifiers — FND-020), the selection-else-filtered
  export/print scope rule and job payload contract (carrying the full
  directional sort, FND-021), the per-user grid preference keys (recent
  searches and last view — FND-017/FND-018, DB-S13), displayed-column live
  search, and deep-link resolution (links are references, never grants).
"""

from mentorapp.api.crm_writes import (
    CrmWriteOutcome,
    WriteApplied,
    WriteDeferred,
    WriteRefused,
    crm_fault_cause,
    crm_write_retry_job,
    crm_write_through,
    integration_credential_from_env,
)
from mentorapp.api.edit_safety import (
    AlreadyCurrent,
    DirtyWindowGuard,
    EditCollisionSwitch,
    EditorWindows,
    FieldConflict,
    ManualMerge,
    RetrySave,
    SaveNotice,
    resolve_concurrent_save_conflict,
    surface_needs_refresh,
)
from mentorapp.api.envelope import ApiError, Envelope, field_error, ok, request_error
from mentorapp.api.errors import (
    ApiValidationError,
    DuplicateCandidatesError,
    RecordNotFoundError,
    StaleRowVersionError,
    register_error_handlers,
)
from mentorapp.api.form_input import (
    PASTE_RESOLVABLE_TYPES,
    PasteResolution,
    auto_format,
    format_email,
    format_phone,
    format_postal_code,
    format_website,
    postal_autofill,
    resolve_paste,
)
from mentorapp.api.form_validation import (
    MESSAGE_PLACEMENT,
    REQUIRED_MARKER,
    FieldSettings,
    ValidationSweep,
    form_label,
    normalized_input,
    place_save_errors,
    sweep_before_save,
    validate_on_exit,
)
from mentorapp.api.grid_surface import (
    GRID_SURFACE,
    AggregateSpec,
    ExplicitSelection,
    ExportScope,
    FallbackToLastUsed,
    FilteredSetSelection,
    GridDocumentRequest,
    GridLink,
    LinkAccessDenied,
    OpenLinkedView,
    Selection,
    SortKey,
    aggregate_expressions,
    export_job_payload,
    grid_search_filter,
    group_row_aggregates,
    hidden_rows_confirmation,
    hidden_selection_count,
    last_view_preference_key,
    parse_selection,
    print_job_payload,
    recent_searches_key,
    remember_search,
    resolve_export_scope,
    resolve_grid_link,
    selection_record_filter,
)
from mentorapp.api.list_engine import (
    count_and_aggregates,
    decode_cursor,
    encode_cursor,
    keyset_page,
    trigram_search_filter,
)
from mentorapp.api.records import registry_for, serialize_record
from mentorapp.api.write_engine import (
    create_record,
    normalize_for_match,
    partial_update,
    validate_value,
)

__all__ = [
    "GRID_SURFACE",
    "MESSAGE_PLACEMENT",
    "PASTE_RESOLVABLE_TYPES",
    "REQUIRED_MARKER",
    "AggregateSpec",
    "AlreadyCurrent",
    "ApiError",
    "ApiValidationError",
    "CrmWriteOutcome",
    "DirtyWindowGuard",
    "DuplicateCandidatesError",
    "EditCollisionSwitch",
    "EditorWindows",
    "Envelope",
    "ExplicitSelection",
    "ExportScope",
    "FallbackToLastUsed",
    "FieldConflict",
    "FieldSettings",
    "FilteredSetSelection",
    "GridDocumentRequest",
    "GridLink",
    "LinkAccessDenied",
    "ManualMerge",
    "OpenLinkedView",
    "PasteResolution",
    "RecordNotFoundError",
    "RetrySave",
    "SaveNotice",
    "Selection",
    "SortKey",
    "StaleRowVersionError",
    "ValidationSweep",
    "WriteApplied",
    "WriteDeferred",
    "WriteRefused",
    "aggregate_expressions",
    "auto_format",
    "count_and_aggregates",
    "create_record",
    "crm_fault_cause",
    "crm_write_retry_job",
    "crm_write_through",
    "decode_cursor",
    "encode_cursor",
    "export_job_payload",
    "field_error",
    "form_label",
    "format_email",
    "format_phone",
    "format_postal_code",
    "format_website",
    "grid_search_filter",
    "group_row_aggregates",
    "hidden_rows_confirmation",
    "hidden_selection_count",
    "integration_credential_from_env",
    "keyset_page",
    "last_view_preference_key",
    "normalize_for_match",
    "normalized_input",
    "ok",
    "parse_selection",
    "partial_update",
    "place_save_errors",
    "postal_autofill",
    "print_job_payload",
    "recent_searches_key",
    "register_error_handlers",
    "registry_for",
    "remember_search",
    "request_error",
    "resolve_concurrent_save_conflict",
    "resolve_export_scope",
    "resolve_grid_link",
    "resolve_paste",
    "selection_record_filter",
    "serialize_record",
    "surface_needs_refresh",
    "sweep_before_save",
    "trigram_search_filter",
    "validate_on_exit",
    "validate_value",
]
