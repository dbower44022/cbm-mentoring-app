"""Grid server API surface design (WTK-042): aggregation, export, search,
selection scope, deep links (REQ-020, REQ-023, REQ-026, REQ-027, REQ-028).

Executable design, per the repo's no-MD rule: the contracts and decision
logic below ARE the specification WTK-047 (endpoints), WTK-050 (deep-link
enforcement) and WTK-051 (tests) build against. The grid/view entities are
WTK-041 (in flight), so nothing here imports a grid table — view state
arrives as wire-shaped values and identity is plain IDs, exactly as
``edit_safety`` speaks the write contract without owning a window.

The surface is five endpoint contracts (:data:`GRID_SURFACE`), each riding
the shared engines — never re-implementing them:

- **Rows** — one keyset page (``list_engine.keyset_page``): view filters,
  plus the live search predicate layered ON TOP (REQ-020), never replacing
  them.
- **Aggregates** (REQ-026) — a SEPARATE query the client issues in parallel
  with the first page (DB-S8 rule 2): ``totalCount`` + footer aggregates
  over the ENTIRE filtered set via ``list_engine.count_and_aggregates``,
  and group-row aggregates via :func:`group_row_aggregates` — same filters,
  never cursor-bounded. :func:`aggregate_expressions` is the one validator
  between a view's declared aggregates and SQL.
- **Export / print** (REQ-027) — both may exceed the 10-second contract
  threshold (DB-S11), so the endpoint NEVER streams a document: it enqueues
  the existing ``automation.artifact_jobs`` job type and answers with the
  ``jobID``; progress reaches the status bar through ``GET /jobs/{jobID}``
  and completion through the change feed — no second notification path.
  :func:`export_job_payload` / :func:`print_job_payload` fix the payload
  contract: the resolved view rendering (columns in display order, the FULL
  directional sort — every key, in priority order, FND-021) travels IN the
  payload so the artifact matches what the grid showed, and scope is the
  :func:`resolve_export_scope` rule — the selection if one exists, else the
  entire filtered set.
- **Deep-link resolution** (REQ-028) — a grid URL names the grid and view;
  :func:`resolve_grid_link` is the whole decision: links are references,
  never grants.

Selection (REQ-023) is a first-class wire value (:func:`parse_selection`):
``explicit`` carries IDs; ``filteredSet`` means "everything the current
filters match" MINUS explicit exclusions — select-all over the whole
filtered result set without ever shipping every ID through the client.
Record identifiers are opaque data-source strings end-to-end (FND-020):
bounded, never parsed for meaning.
"""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from mentorapp.api.envelope import field_error
from mentorapp.api.errors import ApiValidationError
from mentorapp.api.list_engine import trigram_search_filter
from mentorapp.api.records import columns_by_field_name, primary_key_field
from mentorapp.automation.artifact_jobs import EXPORT_JOB_TYPE, PRINT_JOB_TYPE
from mentorapp.observability import get_logger

log = get_logger(__name__)

CODE_INVALID_SELECTION = "invalidSelection"
CODE_UNKNOWN_AGGREGATE_FUNCTION = "unknownAggregateFunction"
CODE_UNKNOWN_AGGREGATE_FIELD = "unknownAggregateField"
CODE_UNSUPPORTED_EXPORT_FORMAT = "unsupportedExportFormat"
CODE_INVALID_SORT = "invalidSort"


# --- The endpoint contracts -------------------------------------------------------


@dataclass(frozen=True)
class EndpointContract:
    """One grid endpoint's contract row.

    ``over_ten_seconds`` is the DB-S11 judgment DECLARED here, not discovered
    in production: True means the endpoint enqueues and answers ``jobID``;
    False means it answers inline and a slow response is a defect.
    """

    method: str
    path: str
    summary: str
    over_ten_seconds: bool


GRID_ROWS: Final = EndpointContract(
    "GET",
    "/grids/{gridId}/rows",
    "One keyset page: view filters + optional search, cursor in meta (DB-S8).",
    over_ten_seconds=False,
)
GRID_AGGREGATES: Final = EndpointContract(
    "GET",
    "/grids/{gridId}/aggregates",
    "totalCount, footer and group aggregates over the ENTIRE filtered set; "
    "issued in parallel with the first rows page (REQ-026).",
    over_ten_seconds=False,
)
GRID_EXPORT: Final = EndpointContract(
    "POST",
    "/grids/{gridId}/export",
    "Enqueue an export job (CSV/Excel, formatted/raw, selection-else-filtered); "
    "answers jobID, progress via /jobs/{jobID} + change feed (REQ-027).",
    over_ten_seconds=True,
)
GRID_PRINT: Final = EndpointContract(
    "POST",
    "/grids/{gridId}/print",
    "Enqueue a print-document job over the same scope rule as export (REQ-027).",
    over_ten_seconds=True,
)
GRID_LINK_RESOLUTION: Final = EndpointContract(
    "GET",
    "/grids/{gridId}/link",
    "Resolve a deep link's viewId to what THIS user actually opens (REQ-028).",
    over_ten_seconds=False,
)

GRID_SURFACE: Final = (
    GRID_ROWS,
    GRID_AGGREGATES,
    GRID_EXPORT,
    GRID_PRINT,
    GRID_LINK_RESOLUTION,
)


# --- Live search over displayed columns (REQ-020) ---------------------------------

# Below three characters the grid stays un-searched — a one-letter needle over
# trigram indexes is noise for the user and a seq-scan invitation for the
# planner; the box simply hasn't "started" yet.
MIN_SEARCH_LENGTH: Final = 3

# The action bar remembers the last five searches per grid (REQ-020), persisted
# through the one preference mechanism (FND-017, DB-S13) — never a new table
# or column.
RECENT_SEARCH_LIMIT: Final = 5


def recent_searches_key(grid_id: str) -> str:
    """The ``userPreference`` key holding one grid's recent-search list.

    The single home for remembered searches (FND-017, DB-S13) — storage
    deliberately tables no per-user grid state.
    """
    return f"grid.{grid_id}.recentSearches"


def last_view_preference_key(grid_id: str) -> str:
    """The ``userPreference`` key persisting a grid's last-displayed view.

    The view choice is the ONE long-term piece of grid state (REQ-017,
    REQ-031), and per-user state rides the one preference mechanism
    (FND-018, DB-S13) — never a table or column. Canonical here so this
    surface's deep-link fallback and the UI panel (``ui.grid_panel``
    re-exports it) can never format the key differently.
    """
    return f"grid.{grid_id}.lastView"


def remember_search(previous: Sequence[str], search_text: str) -> list[str]:
    """The next recent-search list after one search runs: most-recent first.

    A repeated needle moves to the front instead of duplicating; the list
    never exceeds :data:`RECENT_SEARCH_LIMIT`. Sub-minimum text never ran a
    search, so it is never remembered.
    """
    needle = search_text.strip()
    if len(needle) < MIN_SEARCH_LENGTH:
        return list(previous[:RECENT_SEARCH_LIMIT])
    kept = [entry for entry in previous if entry != needle]
    return [needle, *kept][:RECENT_SEARCH_LIMIT]


def grid_search_filter(
    session: Session,
    entity_cls: type[Any],
    entity_type: str,
    search_text: str,
    *,
    displayed_fields: Sequence[str],
) -> ColumnElement[bool] | None:
    """The live-search predicate a grid read APPENDS to the view's own filters.

    ``None`` below :data:`MIN_SEARCH_LENGTH` — the grid is simply unsearched.
    The scope is the view's displayed columns intersected with the registry's
    searchable set (REQ-020 names the displayed columns; ``searchableFlag``
    names what a trigram index can actually serve — the intersection is the
    honest contract). Layering is the caller's ``AND``: search filters ON TOP
    of the view's filters, never instead of them. Fails per-field
    (``searchNotSupported``) when no displayed column is searchable.
    """
    if len(search_text.strip()) < MIN_SEARCH_LENGTH:
        return None
    return trigram_search_filter(
        session, entity_cls, entity_type, search_text, within=displayed_fields
    )


# --- Aggregates over the entire filtered set (REQ-026) ----------------------------

# The aggregate vocabulary a view may declare per column. Deliberately closed:
# every name maps to one SQL function, so a view row can never smuggle SQL.
ALLOWED_AGGREGATE_FUNCTIONS: Final = frozenset({"sum", "avg", "min", "max", "count"})

_SQL_AGGREGATES: Final = {
    "sum": func.sum,
    "avg": func.avg,
    "min": func.min,
    "max": func.max,
    "count": func.count,
}


@dataclass(frozen=True)
class AggregateSpec:
    """One footer/group aggregate a view declares: a function over one field."""

    function: str
    field_name: str

    @property
    def label(self) -> str:
        """The wire key this aggregate answers under, e.g. ``max:cityName``.

        Function-qualified so two aggregates over one field never collide,
        and the client needs no second lookup to render the footer cell.
        """
        return f"{self.function}:{self.field_name}"


def aggregate_expressions(
    entity_cls: type[Any], specs: Sequence[AggregateSpec]
) -> dict[str, ColumnElement[Any]]:
    """Validate a view's aggregate specs into ``count_and_aggregates`` input.

    The ONE gate between view-declared aggregates and SQL. Reports every bad
    spec in one round trip (unknown function, unknown field) — never
    first-failure-only.
    """
    columns = columns_by_field_name(entity_cls)
    errors = []
    expressions: dict[str, ColumnElement[Any]] = {}
    for spec in specs:
        if spec.function not in ALLOWED_AGGREGATE_FUNCTIONS:
            errors.append(
                field_error(
                    spec.field_name,
                    CODE_UNKNOWN_AGGREGATE_FUNCTION,
                    f"'{spec.function}' is not an aggregate this surface computes.",
                )
            )
            continue
        column = columns.get(spec.field_name)
        if column is None:
            errors.append(
                field_error(
                    spec.field_name,
                    CODE_UNKNOWN_AGGREGATE_FIELD,
                    f"'{spec.field_name}' is not a field of this entity.",
                )
            )
            continue
        expressions[spec.label] = _SQL_AGGREGATES[spec.function](column)
    if errors:
        raise ApiValidationError(errors)
    return expressions


def group_row_aggregates(
    session: Session,
    entity_cls: type[Any],
    *,
    group_by_field: str,
    specs: Sequence[AggregateSpec] = (),
    filters: Sequence[ColumnElement[bool]] = (),
    include_deleted: bool = False,
) -> list[dict[str, Any]]:
    """Per-group counts + aggregates over the ENTIRE filtered set (REQ-026).

    The group-row companion to ``count_and_aggregates``: same filters as the
    page fetch, never cursor-bounded, central deleted-row exclusion (DB-S3).
    Each row answers ``{"groupValue": ..., "totalCount": ..., <label>: ...}``;
    ordered by group value so the grid's group rows land in render order.
    Fails like :func:`aggregate_expressions` on a bad spec; a bad group field
    is an unknown-aggregate-field failure too.
    """
    columns = columns_by_field_name(entity_cls)
    group_col = columns.get(group_by_field)
    if group_col is None:
        raise ApiValidationError(
            [
                field_error(
                    group_by_field,
                    CODE_UNKNOWN_AGGREGATE_FIELD,
                    f"'{group_by_field}' is not a field of this entity.",
                )
            ]
        )
    labeled = [
        group_col.label("groupValue"),
        func.count().label("totalCount"),
        *(
            expression.label(name)
            for name, expression in aggregate_expressions(entity_cls, specs).items()
        ),
    ]
    stmt = select(*labeled).select_from(entity_cls).where(*filters)
    if not include_deleted:
        stmt = stmt.where(columns["deletedAt"].is_(None))
    stmt = stmt.group_by(group_col).order_by(group_col)
    return [dict(row) for row in session.execute(stmt).mappings()]


# --- Selection scope (REQ-023) -----------------------------------------------------

SELECTION_EXPLICIT: Final = "explicit"
SELECTION_FILTERED_SET: Final = "filteredSet"

# Record identifiers are opaque data-source values end-to-end (FND-020): grid
# data sources expose CRM records whose IDs are not UUIDs, and a future source
# may expose any stable token. This surface never parses one for meaning — it
# only bounds the value, exactly matching storage's soft-reference rationale
# (``gridSessionState`` stores them as text, like ``appUser.crmUserID``).
RECORD_ID_MAX_LENGTH: Final = 200


def _record_id_is_valid(value: object) -> bool:
    """The FND-020 bound: non-empty text, ≤200 chars, no control characters."""
    if not isinstance(value, str) or not value or len(value) > RECORD_ID_MAX_LENGTH:
        return False
    # Cc covers ASCII AND C1 control characters — a record ID is a token, and
    # a control character only ever arrives via injection or corruption.
    return not any(unicodedata.category(ch) == "Cc" for ch in value)


@dataclass(frozen=True)
class ExplicitSelection:
    """Rows the user picked one by one (click / ctrl / shift).

    ``record_ids`` are opaque data-source identifiers (FND-020) — bounded
    strings, never parsed.
    """

    record_ids: tuple[str, ...]


@dataclass(frozen=True)
class FilteredSetSelection:
    """Select-all: the ENTIRE filtered result set, minus explicit deselects.

    Carrying exclusions instead of inclusions is the whole point — the
    selection stays honest at any set size without the client ever holding
    every ID, and a row inserted after select-all is legitimately IN.
    """

    excluded_record_ids: tuple[str, ...] = ()


Selection = ExplicitSelection | FilteredSetSelection


def parse_selection(payload: dict[str, Any]) -> Selection:
    """Decode the one wire shape every scoped action sends.

    ``{"selectionKind": "explicit", "recordIds": [...]}`` or
    ``{"selectionKind": "filteredSet", "excludedRecordIds": [...]}``.
    Every entry is an opaque data-source identifier (FND-020): validated
    only for bounds (non-empty, at most :data:`RECORD_ID_MAX_LENGTH`
    characters, no control characters), never parsed for meaning. Anything
    else — unknown kind, out-of-bounds entries — fails per-field so the
    client learns exactly which member was malformed.
    """
    kind = payload.get("selectionKind")
    if kind == SELECTION_EXPLICIT:
        ids_field, cls = "recordIds", ExplicitSelection
    elif kind == SELECTION_FILTERED_SET:
        ids_field, cls = "excludedRecordIds", FilteredSetSelection
    else:
        raise ApiValidationError(
            [
                field_error(
                    "selectionKind",
                    CODE_INVALID_SELECTION,
                    f"selectionKind must be '{SELECTION_EXPLICIT}' or "
                    f"'{SELECTION_FILTERED_SET}'.",
                )
            ]
        )
    raw = payload.get(ids_field) or []
    if not all(_record_id_is_valid(value) for value in raw):
        raise ApiValidationError(
            [
                field_error(
                    ids_field,
                    CODE_INVALID_SELECTION,
                    f"every entry in {ids_field} must be a record identifier: "
                    f"non-empty text, at most {RECORD_ID_MAX_LENGTH} characters, "
                    "no control characters.",
                )
            ]
        )
    return cls(tuple(raw))


def selection_record_filter(
    selection: Selection, entity_cls: type[Any]
) -> ColumnElement[bool] | None:
    """The predicate a scoped action APPENDS to the grid's own filters.

    Explicit → membership; filteredSet → only the exclusions (the "entire
    filtered set" part IS the caller's filters — this never re-states them).
    ``None`` means the selection adds no predicate at all: select-all with
    nothing deselected is exactly the filtered set.
    """
    _, pk_col = primary_key_field(entity_cls)
    if isinstance(selection, ExplicitSelection):
        return pk_col.in_(selection.record_ids)
    if not selection.excluded_record_ids:
        return None
    return pk_col.notin_(selection.excluded_record_ids)


def hidden_selection_count(
    session: Session,
    entity_cls: type[Any],
    record_ids: Sequence[str],
    *,
    filters: Sequence[ColumnElement[bool]] = (),
) -> int:
    """How many explicitly selected rows the CURRENT filter no longer shows.

    REQ-023: changing search/filter keeps the selection; the status bar cites
    this number and action confirmations spell it out. ``record_ids`` are the
    selection's opaque data-source identifiers (FND-020). Counts live rows
    only (DB-S3) — a row soft-deleted since selection is gone, not hidden.
    """
    if not record_ids:
        return 0
    columns = columns_by_field_name(entity_cls)
    _, pk_col = primary_key_field(entity_cls)
    visible = session.execute(
        select(func.count())
        .select_from(entity_cls)
        .where(pk_col.in_(record_ids), columns["deletedAt"].is_(None), *filters)
    ).scalar_one()
    return len(record_ids) - int(visible)


def hidden_rows_confirmation(hidden_count: int, action_label: str) -> str | None:
    """The confirmation sentence when an action covers filtered-out rows.

    ``None`` when nothing is hidden — the standard confirmation needs no
    addendum. Educate voice: says what the action will include and why the
    user can't currently see those rows.
    """
    if hidden_count <= 0:
        return None
    rows = "1 selected row" if hidden_count == 1 else f"{hidden_count} selected rows"
    return (
        f"{action_label} will include {rows} the current filter is hiding. "
        "They are still selected — clear the selection first if you meant "
        "only what you can see."
    )


# --- Export & print (REQ-027, DB-S11) ----------------------------------------------

EXPORT_FORMATS: Final = frozenset({"csv", "excel"})

SCOPE_SELECTION: Final = "selection"
SCOPE_FILTERED_SET: Final = "filteredSet"


@dataclass(frozen=True)
class ExportScope:
    """The resolved record scope riding in an export/print job payload.

    IDs are the selection's opaque data-source identifiers (FND-020),
    carried verbatim.
    """

    scope_kind: Literal["selection", "filteredSet"]
    record_ids: tuple[str, ...] = ()
    excluded_record_ids: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        """Wire fragment: IDs verbatim, absent members omitted (JSONB-clean)."""
        payload: dict[str, Any] = {"scopeKind": self.scope_kind}
        if self.record_ids:
            payload["recordIds"] = list(self.record_ids)
        if self.excluded_record_ids:
            payload["excludedRecordIds"] = list(self.excluded_record_ids)
        return payload


def resolve_export_scope(selection: Selection | None) -> ExportScope:
    """THE selection-else-filtered rule (REQ-027), decided in exactly one place.

    A non-empty explicit selection exports the selection; no selection (or an
    empty one) exports the ENTIRE filtered set; select-all exports the
    filtered set carrying its exclusions.
    """
    if isinstance(selection, ExplicitSelection) and selection.record_ids:
        return ExportScope(SCOPE_SELECTION, record_ids=selection.record_ids)
    if isinstance(selection, FilteredSetSelection):
        return ExportScope(
            SCOPE_FILTERED_SET, excluded_record_ids=selection.excluded_record_ids
        )
    return ExportScope(SCOPE_FILTERED_SET)


# The wire vocabulary for a sort key's direction, and the sort-depth cap. The
# grid never offers a fourth key (REQ-025: primary + two shift-click
# secondaries), so a deeper request is malformed, not ambitious.
SORT_KEY_DIRECTIONS: Final = ("asc", "desc")
MAX_SORT_KEYS: Final = 3


@dataclass(frozen=True)
class SortKey:
    """One directional key of a document request's sort order (FND-021).

    The wire form of the one sort shape the layers share: ``field`` is
    storage's ``sortSpec.sortFieldName``, ``direction`` (``asc``/``desc``,
    the UI badge vocabulary) its ``sortDirection``, and a key's 1-based
    tuple position IS its ``sortPosition`` — priority is order. Defined
    here rather than imported because the API layer cannot reach into
    ``ui.grid_panel`` and the storage row carries persistence columns the
    wire never ships; the three shapes are ONE contract, and this is its
    wire equivalent.
    """

    field: str
    direction: str


def _validated_sort_keys(sort_keys: Sequence[SortKey]) -> list[dict[str, str]]:
    """The payload fragment for a request's full directional sort (FND-021).

    Rejects more than :data:`MAX_SORT_KEYS` keys and unknown directions,
    per-field like every other gate on this surface, reporting every bad
    key in one round trip.
    """
    errors = []
    if len(sort_keys) > MAX_SORT_KEYS:
        errors.append(
            field_error(
                "sortKeys",
                CODE_INVALID_SORT,
                f"a grid sort carries at most {MAX_SORT_KEYS} keys (REQ-025).",
            )
        )
    errors.extend(
        field_error(
            "sortKeys",
            CODE_INVALID_SORT,
            f"'{key.direction}' is not a sort direction ({' or '.join(SORT_KEY_DIRECTIONS)}).",
        )
        for key in sort_keys
        if key.direction not in SORT_KEY_DIRECTIONS
    )
    if errors:
        raise ApiValidationError(errors)
    return [{"field": key.field, "direction": key.direction} for key in sort_keys]


@dataclass(frozen=True)
class GridDocumentRequest:
    """What an export/print POST resolves to before it becomes a job payload.

    ``columns`` is the view's displayed fields IN DISPLAY ORDER; ``sort_keys``
    the active sort — EVERY key, in priority order, direction included
    (FND-021: a grid sorted three columns descending exports in exactly that
    order, so the REQ-027 guarantee — the artifact matches what the grid
    showed — holds for the full directional sort, not just the primary
    field); ``filter_state`` the view's serialized filters + active search.
    The whole "as the current view shows it" contract travels in the payload,
    so the artifact can never disagree with the grid the user saw.
    ``raw_values`` False renders formatted values (the REQ-027 default).
    """

    entity_type: str
    columns: tuple[str, ...]
    sort_keys: tuple[SortKey, ...]
    filter_state: dict[str, Any]
    scope: ExportScope
    export_format: str = "csv"
    raw_values: bool = False


def _document_payload(request: GridDocumentRequest) -> dict[str, Any]:
    return {
        "entityType": request.entity_type,
        "columns": list(request.columns),
        "sortKeys": _validated_sort_keys(request.sort_keys),
        "filterState": request.filter_state,
        "scope": request.scope.as_payload(),
        "rawValues": request.raw_values,
    }


def export_job_payload(request: GridDocumentRequest) -> tuple[str, dict[str, Any]]:
    """``(jobType, jobPayload)`` the export endpoint enqueues (DB-S11).

    The job type IS ``artifact_jobs.EXPORT_JOB_TYPE`` — the queue handler and
    this contract can never name the document differently. Rejects a format
    outside :data:`EXPORT_FORMATS` per-field; the endpoint then answers the
    ``jobID`` and the client follows ``GET /jobs/{jobID}`` + the change feed.
    """
    if request.export_format not in EXPORT_FORMATS:
        raise ApiValidationError(
            [
                field_error(
                    "exportFormat",
                    CODE_UNSUPPORTED_EXPORT_FORMAT,
                    f"exportFormat must be one of: {', '.join(sorted(EXPORT_FORMATS))}.",
                )
            ]
        )
    return EXPORT_JOB_TYPE, {
        **_document_payload(request),
        "exportFormat": request.export_format,
    }


def print_job_payload(request: GridDocumentRequest) -> tuple[str, dict[str, Any]]:
    """``(jobType, jobPayload)`` for print: same scope rule, no format choice —
    the print document renders formatted values by definition."""
    return PRINT_JOB_TYPE, _document_payload(request)


# --- Deep-link resolution (REQ-028) -------------------------------------------------


@dataclass(frozen=True)
class GridLink:
    """What a grid URL names, plus the facts resolution needs about it.

    ``view_owner_id`` is ``None`` for a system view. The resolver never
    touches storage — the endpoint (WTK-047/WTK-050) looks these facts up and
    the decision stays a pure, testable rule.
    """

    grid_id: str
    view_id: uuid.UUID
    view_owner_id: uuid.UUID | None


@dataclass(frozen=True)
class OpenLinkedView:
    """The link opens exactly what it names."""

    view_id: uuid.UUID


@dataclass(frozen=True)
class FallbackToLastUsed:
    """The grid opens, the named view does not: last-used view + why.

    ``view_id`` ``None`` means the recipient's last-view preference is unset
    — or stale, naming a view that no longer exists — so the grid opens on
    its default view, and the notice still explains.
    """

    view_id: uuid.UUID | None
    notice: str


@dataclass(frozen=True)
class LinkAccessDenied:
    """No data-source permission: the link grants nothing (REQ-028)."""

    notice: str


def resolve_grid_link(
    link: GridLink,
    *,
    requester_id: uuid.UUID,
    has_data_source_access: bool,
    last_view_preference: uuid.UUID | None,
) -> OpenLinkedView | FallbackToLastUsed | LinkAccessDenied:
    """Decide what a deep link opens for THIS requester (REQ-028).

    Links are references, not grants: no data-source access → denied,
    whoever sent it. A system view (no owner) or the requester's own view
    opens as named. Another user's private view is invisible by design, so
    the recipient lands on their last-used view with an explanation — never
    a blank grid, never someone else's private state.

    ``last_view_preference`` is the read path's preference value: the
    last-used view persists as the requester's ``userPreference`` row under
    :func:`last_view_preference_key` (FND-018, DB-S13 — preference state,
    not a table). The endpoint reads that key and passes ``None`` when it is
    unset OR when it names a view that no longer exists, so the fallback
    lands on the grid's default view — WTK-042's original fallback
    semantics, now consuming the one preference mechanism.
    """
    if not has_data_source_access:
        return LinkAccessDenied(
            "This link points at data you don't have access to. Links open "
            "views; they don't grant permission — ask an administrator if "
            "you need this data source."
        )
    if link.view_owner_id is None or link.view_owner_id == requester_id:
        return OpenLinkedView(link.view_id)
    log.info(
        "deep link to another user's private view fell back",
        extra={
            "context": {
                "userId": str(requester_id),
                "gridId": link.grid_id,
                "viewId": str(link.view_id),
            }
        },
    )
    return FallbackToLastUsed(
        last_view_preference,
        "This link points at another person's private view, so it opened "
        "your own last-used view instead. Ask them to share the view if you "
        "need their exact setup.",
    )
