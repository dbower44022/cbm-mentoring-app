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
"""

from mentorapp.api.envelope import ApiError, Envelope, field_error, ok, request_error
from mentorapp.api.errors import (
    ApiValidationError,
    DuplicateCandidatesError,
    RecordNotFoundError,
    StaleRowVersionError,
    register_error_handlers,
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
    "ApiError",
    "ApiValidationError",
    "DuplicateCandidatesError",
    "Envelope",
    "RecordNotFoundError",
    "StaleRowVersionError",
    "count_and_aggregates",
    "create_record",
    "decode_cursor",
    "encode_cursor",
    "field_error",
    "keyset_page",
    "normalize_for_match",
    "ok",
    "partial_update",
    "register_error_handlers",
    "registry_for",
    "request_error",
    "serialize_record",
    "trigram_search_filter",
]
