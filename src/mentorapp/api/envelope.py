"""The single response envelope every endpoint speaks (REQ-059, DB-S12).

Shape: ``{"data": ..., "meta": ..., "errors": ...}``.

- ``data`` — the payload. On failures it may still carry a body the client
  needs to recover: the current record on a concurrency conflict, the
  candidate records on a duplicate-create rejection.
- ``meta`` — always an object, never null. List endpoints put counts,
  aggregates, and the keyset cursor here (DB-S8) so the payload shape never
  varies with pagination.
- ``errors`` — ``None`` on success; on failure a non-empty list of structured
  entries (see ``ApiError``). A multi-field save reports ALL failures in one
  round trip, never first-failure-only.

The error shape is part of the server-driven rendering contract: the UI maps
``fieldName`` to the form control, switches on ``code``, and shows ``message``.
"""

from __future__ import annotations

from typing import Any, TypedDict


class ApiError(TypedDict):
    """One structured error entry in ``errors[]``.

    ``fieldName`` is the registry field the failure belongs to, or ``None``
    for request-level failures (not-found, conflict, internal). ``code`` is
    machine-readable and stable; ``message`` is for humans and may change.
    """

    # Wire names are the contract — camelCase per the data-model standard (DB-R2),
    # matching the schema-registry vocabulary the UI already speaks.
    fieldName: str | None
    code: str
    message: str


class Envelope(TypedDict):
    """The one response shape. Constructed only via ``ok`` and ``fail``."""

    data: Any
    meta: dict[str, Any]
    errors: list[ApiError] | None


def ok(data: Any = None, meta: dict[str, Any] | None = None) -> Envelope:
    """Success envelope: payload in ``data``, ``errors`` is ``None``."""
    return {"data": data, "meta": meta or {}, "errors": None}


def fail(
    errors: list[ApiError],
    *,
    data: Any = None,
    meta: dict[str, Any] | None = None,
) -> Envelope:
    """Failure envelope: every entry structured, ``data`` only when the client
    needs a recovery body (current record on 409, duplicate candidates).
    """
    return {"data": data, "meta": meta or {}, "errors": errors}


def field_error(field_name: str, code: str, message: str) -> ApiError:
    """One per-field validation failure, keyed to a schema-registry field."""
    return {"fieldName": field_name, "code": code, "message": message}


def request_error(code: str, message: str) -> ApiError:
    """One request-level failure that belongs to no single field."""
    return {"fieldName": None, "code": code, "message": message}
