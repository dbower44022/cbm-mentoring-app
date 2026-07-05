"""The shared list-read engine (REQ-055, DB-S8): every grid read speaks this.

Three rules, built once:

1. Keyset (seek) pagination, never OFFSET — a page is "the next N rows after
   cursor X", where the cursor is the sort value plus the record ID as
   tiebreak. Every page is an equal-cost indexed seek, stable under
   concurrent inserts; the UUIDv7 key is always available as the tiebreak.
2. Counts and aggregates are a separate query over the ENTIRE filtered set —
   issued independently of the page fetch so rows render first and the count
   fills in when it lands. The cursor never applies to it.
3. Search is server-side over the registry-declared searchable columns
   (``searchableFlag``, opt-in per DB-S6) — on Postgres the contains
   predicates are served by pg_trgm GIN indexes; on SQLite (tests) the same
   expression degrades to a scan. The contract is portable; the plan is not.

Deleted rows are excluded here, centrally (DB-S3) — never per endpoint.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.orm import Session

from mentorapp.api.envelope import field_error
from mentorapp.api.errors import ApiValidationError
from mentorapp.api.records import (
    attribute_keys_by_field_name,
    columns_by_field_name,
    primary_key_field,
    record_id_of,
    registry_for,
)

CODE_INVALID_CURSOR = "invalidCursor"
CODE_UNSORTABLE_FIELD = "unsortableField"
CODE_SEARCH_NOT_SUPPORTED = "searchNotSupported"

# One page never exceeds this, whatever the client asks for: the grid standard
# is infinite scroll, so a bigger page buys nothing but latency.
MAX_PAGE_SIZE = 200


def encode_cursor(sort_value: Any, record_id: uuid.UUID) -> str:
    """Opaque keyset cursor: the last row's sort value + its record ID (DB-S8).

    Datetimes are type-tagged so they compare as datetimes again after decode;
    other sort values (str/int/float) survive JSON natively. base64url keeps
    the cursor query-string-safe and deliberately opaque — clients store it,
    never parse it.
    """
    if isinstance(sort_value, datetime):
        payload: dict[str, Any] = {"t": "datetime", "v": sort_value.isoformat()}
    else:
        payload = {"t": "plain", "v": sort_value}
    payload["id"] = str(record_id)
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[Any, uuid.UUID]:
    """Decode a cursor back to ``(sort_value, record_id)``.

    A cursor the engine did not mint (truncated, tampered, wrong entity's key
    shape) is a per-field validation failure, not a 500.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        value = payload["v"]
        if payload["t"] == "datetime":
            value = datetime.fromisoformat(value)
        return value, uuid.UUID(payload["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ApiValidationError(
            [field_error("cursor", CODE_INVALID_CURSOR, "The cursor is not valid.")]
        ) from exc


def keyset_page(
    session: Session,
    entity_cls: type[Any],
    *,
    sort_field: str,
    page_size: int,
    cursor: str | None = None,
    filters: Sequence[ColumnElement[bool]] = (),
    include_deleted: bool = False,
) -> tuple[list[Any], str | None]:
    """One page of records plus the cursor for the next, or ``None`` at the end.

    The seek predicate is the expanded form ``sort > v OR (sort = v AND id >
    i)`` rather than a row-value comparison — identical index-driven plan on
    Postgres, and it also runs on the SQLite test dialect. ``include_deleted``
    exists only for admin/restore surfaces (DB-S3). Fails with
    :class:`ApiValidationError` on an unknown sort field or a bad cursor.
    """
    columns = columns_by_field_name(entity_cls)
    sort_col = columns.get(sort_field)
    if sort_col is None:
        raise ApiValidationError(
            [field_error(sort_field, CODE_UNSORTABLE_FIELD, "Unknown sort field.")]
        )
    _, pk_col = primary_key_field(entity_cls)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))

    stmt = select(entity_cls).where(*filters)
    if not include_deleted:
        stmt = stmt.where(columns["deletedAt"].is_(None))
    if cursor is not None:
        sort_value, record_id = decode_cursor(cursor)
        stmt = stmt.where(
            or_(sort_col > sort_value, and_(sort_col == sort_value, pk_col > record_id))
        )
    # limit+1 probes for a next page without a second query; the extra row is
    # discarded, never served.
    stmt = stmt.order_by(sort_col, pk_col).limit(page_size + 1)
    rows = list(session.scalars(stmt))

    if len(rows) <= page_size:
        return rows, None
    rows = rows[:page_size]
    last = rows[-1]
    sort_attr_key = attribute_keys_by_field_name(entity_cls)[sort_field]
    return rows, encode_cursor(getattr(last, sort_attr_key), record_id_of(last))


def count_and_aggregates(
    session: Session,
    entity_cls: type[Any],
    *,
    filters: Sequence[ColumnElement[bool]] = (),
    include_deleted: bool = False,
    aggregates: dict[str, ColumnElement[Any]] | None = None,
) -> dict[str, Any]:
    """The ``meta`` fragment for a list read: ``totalCount`` plus named aggregates.

    Deliberately a separate function taking the SAME filters as the page fetch
    (DB-S8 rule 2): the router issues it independently — in parallel with the
    first page — and it always spans the whole filtered set, never one page.
    """
    labeled = [func.count().label("totalCount")] + [
        expression.label(name) for name, expression in (aggregates or {}).items()
    ]
    stmt = select(*labeled).select_from(entity_cls).where(*filters)
    if not include_deleted:
        stmt = stmt.where(columns_by_field_name(entity_cls)["deletedAt"].is_(None))
    return dict(session.execute(stmt).mappings().one())


def trigram_search_filter(
    session: Session,
    entity_cls: type[Any],
    entity_type: str,
    search_text: str,
    *,
    within: Sequence[str] | None = None,
) -> ColumnElement[bool]:
    """A server-side contains-match over the entity's searchable columns.

    The searchable set is declared in the schema registry (``searchableFlag``),
    never hardcoded per endpoint. ``within`` optionally narrows it to a field
    subset — the grid surface passes the view's displayed columns (REQ-020) —
    but can never widen it: a field outside the registry's searchable set has
    no trigram index to serve it. ``%``/``_`` in the user's text are escaped —
    search text is a needle, never a pattern. An empty effective set rejects
    search explicitly rather than silently matching nothing.
    """
    registry = registry_for(session, entity_type)
    columns = columns_by_field_name(entity_cls)
    searchable = [
        columns[name]
        for name, row in registry.items()
        if row.searchable_flag and name in columns and (within is None or name in within)
    ]
    if not searchable:
        raise ApiValidationError(
            [
                field_error(
                    "search",
                    CODE_SEARCH_NOT_SUPPORTED,
                    f"{entity_type} has no searchable fields"
                    + (" among the displayed columns." if within is not None else "."),
                )
            ]
        )
    escaped = search_text.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    needle = f"%{escaped}%"
    return or_(*[column.ilike(needle, escape="\\") for column in searchable])
