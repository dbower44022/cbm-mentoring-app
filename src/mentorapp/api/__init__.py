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
"""

from mentorapp.api.envelope import ApiError, Envelope, field_error, ok, request_error
from mentorapp.api.errors import (
    ApiValidationError,
    DuplicateCandidatesError,
    RecordNotFoundError,
    StaleRowVersionError,
    register_error_handlers,
)

__all__ = [
    "ApiError",
    "ApiValidationError",
    "DuplicateCandidatesError",
    "Envelope",
    "RecordNotFoundError",
    "StaleRowVersionError",
    "field_error",
    "ok",
    "register_error_handlers",
    "request_error",
]
