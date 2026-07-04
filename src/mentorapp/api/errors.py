"""Structured API failures and the handlers that keep them inside the envelope.

The write-contract failure semantics (REQ-059, DB-S12, DB-S4):

- Validation: ``ApiValidationError`` carries EVERY failed field; the handler
  returns 422 with all entries in one round trip. FastAPI's own request
  validation is translated into the same per-field shape and status, so the
  client sees exactly one validation contract regardless of which layer
  caught the failure.
- Concurrency: ``StaleRowVersionError`` returns 409 with the CURRENT record
  in ``data`` so the client can show merge/refresh or auto-retry a field-level
  PATCH whose field is untouched in the fresh copy.
- Duplicate detection: ``DuplicateCandidatesError`` returns 409 with the
  candidate records in ``data``; the client merges into one or resubmits with
  the recorded override flag.
- Unknowns are logged with full context and returned as an opaque 500 —
  never swallowed, never leaked.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from mentorapp.api.envelope import ApiError, fail, field_error, request_error
from mentorapp.observability import get_logger

log = get_logger(__name__)

# The stable machine-readable code vocabulary. Clients switch on these; adding
# a code is additive, renaming one is a breaking API change.
CODE_VALIDATION: Final = "validationFailed"
CODE_STALE_ROW_VERSION: Final = "staleRowVersion"
CODE_DUPLICATE_CANDIDATES: Final = "duplicateCandidates"
CODE_NOT_FOUND: Final = "notFound"
CODE_INTERNAL: Final = "internalError"


class ApiValidationError(Exception):
    """All per-field failures for one write, reported in one round trip.

    Raisers accumulate every failure (registry validation, normalization,
    option-set membership) before raising — never first-failure-only.
    """

    def __init__(self, errors: list[ApiError]) -> None:
        super().__init__(f"{len(errors)} validation failure(s)")
        self.errors = errors


class StaleRowVersionError(Exception):
    """Optimistic-concurrency conflict (DB-S4): the write's ``rowVersion`` was
    stale. ``current_record`` is the fresh copy served back in ``data``.
    """

    def __init__(self, current_record: dict[str, Any]) -> None:
        super().__init__("stale rowVersion")
        self.current_record = current_record


class DuplicateCandidatesError(Exception):
    """Duplicate-detection rejection on create (REQ-059): ``candidates`` are the
    matching existing records served back in ``data`` for merge-or-override.
    """

    def __init__(self, candidates: list[dict[str, Any]]) -> None:
        super().__init__(f"{len(candidates)} duplicate candidate(s)")
        self.candidates = candidates


class RecordNotFoundError(Exception):
    """The addressed record (or entity) does not exist or is soft-deleted."""

    def __init__(self, entity_type: str, record_id: str) -> None:
        super().__init__(f"{entity_type} {record_id} not found")
        self.entity_type = entity_type
        self.record_id = record_id


def _respond(status_code: int, envelope: dict[str, Any]) -> JSONResponse:
    # jsonable_encoder so recovery bodies (current record, candidates) may carry
    # UUIDs/datetimes without every raiser hand-serializing them.
    return JSONResponse(status_code=status_code, content=jsonable_encoder(envelope))


def register_error_handlers(app: FastAPI) -> None:
    """Install the handlers that keep every failure inside the envelope.

    Called once by the app factory; no endpoint builds its own error response.
    """

    @app.exception_handler(ApiValidationError)
    async def _validation(_request: Request, exc: ApiValidationError) -> JSONResponse:
        return _respond(422, fail(exc.errors))

    @app.exception_handler(RequestValidationError)
    async def _request_validation(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Same shape and status as ApiValidationError: loc[0] is the source
        # ("body"/"query"/"path"), the rest is the field path the UI keys on.
        errors = [
            field_error(
                ".".join(str(part) for part in err["loc"][1:]) or str(err["loc"][0]),
                str(err["type"]),
                str(err["msg"]),
            )
            for err in exc.errors()
        ]
        return _respond(422, fail(errors))

    @app.exception_handler(StaleRowVersionError)
    async def _stale(_request: Request, exc: StaleRowVersionError) -> JSONResponse:
        error = request_error(
            CODE_STALE_ROW_VERSION,
            "The record changed since it was read; the current record is in data.",
        )
        return _respond(409, fail([error], data=exc.current_record))

    @app.exception_handler(DuplicateCandidatesError)
    async def _duplicate(_request: Request, exc: DuplicateCandidatesError) -> JSONResponse:
        error = request_error(
            CODE_DUPLICATE_CANDIDATES,
            "Possible duplicate records exist; candidates are in data. "
            "Merge into one or resubmit with the override flag.",
        )
        return _respond(409, fail([error], data=exc.candidates))

    @app.exception_handler(RecordNotFoundError)
    async def _not_found(_request: Request, exc: RecordNotFoundError) -> JSONResponse:
        error = request_error(
            CODE_NOT_FOUND, f"{exc.entity_type} {exc.record_id} was not found."
        )
        return _respond(404, fail([error]))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never swallowed, never leaked: full context to the structured logger,
        # an opaque envelope to the client.
        log.error(
            "unhandled exception",
            exc_info=exc,
            extra={"context": {"path": request.url.path, "method": request.method}},
        )
        error = request_error(CODE_INTERNAL, "An internal error occurred.")
        return _respond(500, fail([error]))
