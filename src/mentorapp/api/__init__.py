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
- ``grid_surface`` — the grid server API surface design (WTK-042,
  REQ-020/023/026/027/028): the five endpoint contracts with their DB-S11
  over-ten-seconds declarations, whole-filtered-set footer/group aggregates,
  the selection wire shape (explicit vs filteredSet select-all), the
  selection-else-filtered export/print scope rule and job payload contract,
  displayed-column live search, and deep-link resolution (links are
  references, never grants).
"""

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
    aggregate_expressions,
    export_job_payload,
    grid_search_filter,
    group_row_aggregates,
    hidden_rows_confirmation,
    hidden_selection_count,
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
from mentorapp.api.write_engine import create_record, normalize_for_match, partial_update

__all__ = [
    "GRID_SURFACE",
    "AggregateSpec",
    "AlreadyCurrent",
    "ApiError",
    "ApiValidationError",
    "DirtyWindowGuard",
    "DuplicateCandidatesError",
    "EditCollisionSwitch",
    "EditorWindows",
    "Envelope",
    "ExplicitSelection",
    "ExportScope",
    "FallbackToLastUsed",
    "FieldConflict",
    "FilteredSetSelection",
    "GridDocumentRequest",
    "GridLink",
    "LinkAccessDenied",
    "ManualMerge",
    "OpenLinkedView",
    "RecordNotFoundError",
    "RetrySave",
    "SaveNotice",
    "Selection",
    "StaleRowVersionError",
    "aggregate_expressions",
    "count_and_aggregates",
    "create_record",
    "decode_cursor",
    "encode_cursor",
    "export_job_payload",
    "field_error",
    "grid_search_filter",
    "group_row_aggregates",
    "hidden_rows_confirmation",
    "hidden_selection_count",
    "keyset_page",
    "normalize_for_match",
    "ok",
    "parse_selection",
    "partial_update",
    "print_job_payload",
    "recent_searches_key",
    "register_error_handlers",
    "registry_for",
    "remember_search",
    "request_error",
    "resolve_concurrent_save_conflict",
    "resolve_export_scope",
    "resolve_grid_link",
    "selection_record_filter",
    "serialize_record",
    "surface_needs_refresh",
    "trigram_search_filter",
]
