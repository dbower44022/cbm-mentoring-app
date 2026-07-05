"""``/grids`` — the grid server surface: rows, aggregates, export, print (WTK-047).

The build of the WTK-042 design (``mentorapp.api.grid_surface``) for
REQ-020/026/027, riding the shared engines — never re-implementing them:

- ``GET /grids/{gridKey}/rows`` — one keyset page (``list_engine.keyset_page``)
  of the named view: the view's own filters, plus the live-search predicate
  layered ON TOP at three characters and up (REQ-020,
  ``grid_surface.grid_search_filter``), scoped to the view's displayed
  columns. A search that runs is remembered — the recent-search list persists
  as the caller's ``userPreference`` row under
  ``grid_surface.recent_searches_key`` (FND-017, DB-S13) and rides back in
  ``meta.recentSearches`` for the action bar's recall menu.
- ``GET /grids/{gridKey}/aggregates`` — ``totalCount`` plus the view's
  declared footer aggregates over the ENTIRE filtered set
  (``list_engine.count_and_aggregates``), and group rows when the view
  groups (``grid_surface.group_row_aggregates``) — same filters as the rows
  read, never cursor-bounded; the client issues it in parallel with the
  first page (REQ-026, DB-S8 rule 2).
- ``POST /grids/{gridKey}/export`` / ``POST /grids/{gridKey}/print`` — both
  are declared over the ten-second threshold (DB-S11), so they NEVER stream
  a document: they enqueue the ``automation.artifact_jobs`` job type with
  the payload ``grid_surface.export_job_payload``/``print_job_payload`` fix
  (view rendering + full directional sort, FND-021; selection-else-filtered
  scope, REQ-027) and answer the ``jobID``. Status-bar progress follows
  ``GET /jobs/{jobID}``; completion also surfaces through the change feed —
  no second notification path.

Deep-link resolution (``GRID_LINK_RESOLUTION``) is deliberately absent: that
endpoint is WTK-050's build.

Grid views read through admin-authored data sources (DB-S9); the engines
here compose over ORM entities. :class:`GridEntityCatalog` is the seam
between the two — wiring binds each entity-backed data-source key to its
declarative class (the home/records fail-loud seam pattern), so this router
never owns the mapping and an unwired deployment reads as a server error,
never an empty grid.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.envelope import Envelope, field_error, ok
from mentorapp.api.errors import ApiValidationError, RecordNotFoundError
from mentorapp.api.grid_surface import (
    AggregateSpec,
    GridDocumentRequest,
    Selection,
    SortKey,
    aggregate_expressions,
    export_job_payload,
    grid_search_filter,
    group_row_aggregates,
    parse_selection,
    print_job_payload,
    recent_searches_key,
    remember_search,
    resolve_export_scope,
)
from mentorapp.api.list_engine import count_and_aggregates, keyset_page
from mentorapp.api.records import (
    columns_by_field_name,
    primary_key_field,
    serialize_record,
)
from mentorapp.automation.worker import enqueue_job
from mentorapp.observability import get_logger
from mentorapp.storage import DataSource, Grid, GridView, UserPreference

log = get_logger(__name__)

router = APIRouter()

CODE_UNKNOWN_FILTER_FIELD = "unknownFilterField"
CODE_UNSUPPORTED_DATA_SOURCE = "unsupportedDataSource"

# Storage speaks REQ-025's spelled-out vocabulary; the wire (and the job
# payload, FND-021) speaks the UI badge vocabulary. One mapping, here.
_WIRE_DIRECTIONS = {"ascending": "asc", "descending": "desc"}


class GridEntityCatalog(Protocol):
    """Resolve a data-source key to the entity it reads: ``(entityType, class)``.

    ``None`` for a source with no entity backing (a pure admin-SQL source) —
    the caller refuses per-field rather than guessing a table.
    """

    def entity_for(self, data_source_key: str) -> tuple[str, type[Any]] | None:
        """The entity behind one data source, or ``None`` when there is none."""
        ...


def get_grid_entity_catalog() -> GridEntityCatalog:
    """Provide the data-source → entity catalog; wiring binds it, tests override.

    Fail-loud, never an empty default: a missing binding must read as a
    deployment error, not as every grid in the app being empty.
    """
    raise RuntimeError(
        "grid entity catalog provider is not wired; install grids wiring or "
        "override get_grid_entity_catalog."
    )


_SessionDep = Annotated[Session, Depends(get_session)]
_UserDep = Annotated[uuid.UUID, Depends(get_current_user_id)]
_CatalogDep = Annotated[GridEntityCatalog, Depends(get_grid_entity_catalog)]


def view_filter_predicates(
    entity_cls: type[Any], view_filters: dict[str, Any] | None
) -> list[ColumnElement[bool]]:
    """Compile a view's stored ``viewFilters`` document into engine filters.

    The v1 filter vocabulary is deliberately minimal: ``{fieldName: value}``
    means equality, a list value means membership — enough for the seeded
    system views; the REQ-029 ad-hoc operator funnel extends this at its own
    planning item. Unknown fields fail per-field, every failure in one round
    trip (DB-S12).
    """
    if not view_filters:
        return []
    columns = columns_by_field_name(entity_cls)
    errors = []
    predicates: list[ColumnElement[bool]] = []
    for field_name, wanted in view_filters.items():
        column = columns.get(field_name)
        if column is None:
            errors.append(
                field_error(
                    field_name,
                    CODE_UNKNOWN_FILTER_FIELD,
                    f"'{field_name}' is not a field this view's entity has.",
                )
            )
            continue
        predicates.append(column.in_(wanted) if isinstance(wanted, list) else column == wanted)
    if errors:
        raise ApiValidationError(errors)
    return predicates


def _live_grid_and_view(session: Session, grid_key: str, view_id: uuid.UUID) -> GridView:
    """The named view of the named grid, or the honest 404 (grid vs view)."""
    grid = session.scalars(
        select(Grid).where(Grid.grid_key == grid_key, Grid.deleted_at.is_(None))
    ).first()
    if grid is None:
        raise RecordNotFoundError("grid", grid_key)
    view = session.get(GridView, view_id)
    if view is None or view.deleted_at is not None or view.grid_id != grid.grid_id:
        raise RecordNotFoundError("gridView", str(view_id))
    return view


def _view_entity(
    session: Session, view: GridView, catalog: GridEntityCatalog
) -> tuple[str, type[Any]]:
    source = session.get(DataSource, view.data_source_id)
    resolved = catalog.entity_for(source.data_source_key) if source is not None else None
    if resolved is None:
        raise ApiValidationError(
            [
                field_error(
                    "viewId",
                    CODE_UNSUPPORTED_DATA_SOURCE,
                    "this view's data source is not entity-backed; it cannot "
                    "serve the grid surface.",
                )
            ]
        )
    return resolved


def _displayed_field_names(view: GridView) -> list[str]:
    # displayedFields entries are {fieldName, columnWidth, columnFormat};
    # list order IS display order (REQ-018).
    return [entry["fieldName"] for entry in view.displayed_fields]


def _view_predicates(
    session: Session,
    view: GridView,
    entity_type: str,
    entity_cls: type[Any],
    search: str,
) -> list[ColumnElement[bool]]:
    """View filters + the optional search predicate ON TOP — never instead (REQ-020)."""
    predicates = view_filter_predicates(entity_cls, view.view_filters)
    search_predicate = grid_search_filter(
        session,
        entity_cls,
        entity_type,
        search,
        displayed_fields=_displayed_field_names(view),
    )
    if search_predicate is not None:
        predicates.append(search_predicate)
    return predicates


def _remember_recent_search(
    session: Session, user_id: uuid.UUID, grid_key: str, search_text: str
) -> list[str]:
    """Fold one executed search into the caller's recall list (REQ-020, FND-017).

    Reads/writes the ``userPreference`` row under ``recent_searches_key`` —
    the ONE home for per-user grid state (DB-S13); sub-minimum text never ran
    a search, so ``remember_search`` leaves the list untouched.
    """
    key = recent_searches_key(grid_key)
    row = session.scalars(
        select(UserPreference)
        .where(UserPreference.deleted_at.is_(None))
        .where(UserPreference.preference_key == key)
        .where(UserPreference.user_id == user_id)
    ).first()
    previous = list((row.preference_value or {}).get("recentSearches", [])) if row else []
    remembered = remember_search(previous, search_text)
    if remembered == previous:
        return remembered
    if row is None:
        session.add(
            UserPreference(
                user_id=user_id,
                preference_key=key,
                preference_value={"recentSearches": remembered},
                created_by=user_id,
                modified_by=user_id,
            )
        )
    else:
        row.preference_value = {"recentSearches": remembered}
        row.modified_by = user_id
    session.flush()
    return remembered


@router.get("/grids/{grid_key}/rows")
def get_grid_rows(
    grid_key: str,
    view_id: uuid.UUID,
    session: _SessionDep,
    user_id: _UserDep,
    catalog: _CatalogDep,
    search: str = "",
    cursor: str | None = None,
    page_size: int = 50,
) -> Envelope:
    """One keyset page of the view, live search layered on the view's filters.

    Query: ``view_id`` (required), ``search`` (arms at three characters,
    REQ-020), ``cursor``/``page_size`` (DB-S8). Sorted by the view's primary
    sort spec (the engine's keyset order), the entity key as tiebreak.
    ``meta`` carries ``cursor`` (null at the end), ``searchApplied``, and
    ``recentSearches`` — the recall list AFTER this request, persisted per
    user through the one preference mechanism (FND-017). 404 unknown
    grid/view; 422 per-field on bad filters, unsearchable displayed columns,
    or a foreign cursor.
    """
    view = _live_grid_and_view(session, grid_key, view_id)
    entity_type, entity_cls = _view_entity(session, view, catalog)
    predicates = _view_predicates(session, view, entity_type, entity_cls, search)
    search_applied = len(predicates) > len(view_filter_predicates(entity_cls, view.view_filters))
    specs = view.sort_specs
    sort_field = specs[0].sort_field_name if specs else primary_key_field(entity_cls)[0]
    rows, next_cursor = keyset_page(
        session,
        entity_cls,
        sort_field=sort_field,
        page_size=page_size,
        cursor=cursor,
        filters=predicates,
    )
    remembered = _remember_recent_search(session, user_id, grid_key, search)
    session.commit()
    return ok(
        data=[serialize_record(row) for row in rows],
        meta={
            "cursor": next_cursor,
            "searchApplied": search_applied,
            "recentSearches": remembered,
        },
    )


@router.get("/grids/{grid_key}/aggregates")
def get_grid_aggregates(
    grid_key: str,
    view_id: uuid.UUID,
    session: _SessionDep,
    user_id: _UserDep,
    catalog: _CatalogDep,
    search: str = "",
) -> Envelope:
    """Counts and aggregates over the ENTIRE filtered set (REQ-026, DB-S8 rule 2).

    A separate query the client issues in parallel with the first rows page —
    same filters (view + search), NEVER cursor-bounded. ``data.totalCount``
    and ``data.aggregates`` (the view's declared specs, keyed
    ``function:fieldName``) always answer; ``data.groupRows`` answers when
    the view groups, one entry per group value with its own count and
    aggregates. Failures match the rows read, plus per-field on a bad
    aggregate spec.
    """
    view = _live_grid_and_view(session, grid_key, view_id)
    entity_type, entity_cls = _view_entity(session, view, catalog)
    predicates = _view_predicates(session, view, entity_type, entity_cls, search)
    specs = [
        AggregateSpec(entry["function"], entry["fieldName"])
        for entry in (view.view_aggregates or [])
    ]
    totals = count_and_aggregates(
        session,
        entity_cls,
        filters=predicates,
        aggregates=aggregate_expressions(entity_cls, specs),
    )
    total_count = totals.pop("totalCount")
    group_fields = (view.grouping_config or {}).get("groupFields") or []
    group_rows = (
        group_row_aggregates(
            session,
            entity_cls,
            # v1 group rows aggregate the outermost grouping level; nested
            # tree totals are the UI's fold of the same rows (REQ-018).
            group_by_field=group_fields[0],
            specs=specs,
            filters=predicates,
        )
        if group_fields
        else None
    )
    return ok(
        data={"totalCount": total_count, "aggregates": totals, "groupRows": group_rows}
    )


class GridPrintBody(BaseModel):
    """Print POST body: the view, the optional live search, the selection."""

    view_id: uuid.UUID = Field(alias="viewId")
    search: str = ""
    selection: dict[str, Any] | None = None


class GridExportBody(GridPrintBody):
    """Export adds the REQ-027 choices: format, formatted-vs-raw values."""

    export_format: str = Field(default="csv", alias="exportFormat")
    raw_values: bool = Field(default=False, alias="rawValues")


def _document_request(
    session: Session,
    view: GridView,
    entity_type: str,
    body: GridPrintBody,
    *,
    export_format: str = "csv",
    raw_values: bool = False,
) -> GridDocumentRequest:
    """Resolve one export/print POST into the WTK-042 document request.

    The whole "as the current view shows it" contract (REQ-027, FND-021)
    travels in the request: columns in display order, EVERY sort key in
    priority order with its direction, the view filters + active search, and
    the selection-else-filtered scope.
    """
    selection: Selection | None = (
        parse_selection(body.selection) if body.selection is not None else None
    )
    return GridDocumentRequest(
        entity_type=entity_type,
        columns=tuple(_displayed_field_names(view)),
        sort_keys=tuple(
            SortKey(
                spec.sort_field_name,
                _WIRE_DIRECTIONS.get(spec.sort_direction, spec.sort_direction),
            )
            for spec in view.sort_specs
        ),
        filter_state={
            "viewFilters": view.view_filters or {},
            "search": body.search.strip() or None,
        },
        scope=resolve_export_scope(selection),
        export_format=export_format,
        raw_values=raw_values,
    )


def _enqueue_document(
    session: Session,
    user_id: uuid.UUID,
    grid_key: str,
    job_type: str,
    payload: dict[str, Any],
) -> Envelope:
    job = enqueue_job(session, job_type, payload, acting_user_id=user_id)
    session.commit()
    log.info(
        "grid document job enqueued",
        extra={
            "context": {
                "userId": str(user_id),
                "gridId": grid_key,
                "jobID": str(job.job_id),
                "jobType": job_type,
            }
        },
    )
    # The DB-S11 answer: never the document — the jobID, followed at
    # /jobs/{jobID} for status-bar progress; completion also reaches open
    # windows through the change feed.
    return ok(
        data={
            "jobId": job.job_id,
            "jobType": job_type,
            "jobStatus": job.job_status,
            "statusPath": f"/jobs/{job.job_id}",
        }
    )


@router.post("/grids/{grid_key}/export")
def post_grid_export(
    grid_key: str,
    body: GridExportBody,
    session: _SessionDep,
    user_id: _UserDep,
    catalog: _CatalogDep,
) -> Envelope:
    """Enqueue a grid export: CSV/Excel, formatted or raw, view-rendered (REQ-027).

    Scope is the one rule — the selection when one exists, else the entire
    filtered set. Declared over the ten-second threshold (DB-S11), so the
    answer is always ``data.jobId``, never a file. 422 per-field on an
    unsupported ``exportFormat``, a malformed selection, or an over-deep
    sort; 404 unknown grid/view.
    """
    view = _live_grid_and_view(session, grid_key, body.view_id)
    entity_type, _ = _view_entity(session, view, catalog)
    request = _document_request(
        session,
        view,
        entity_type,
        body,
        export_format=body.export_format,
        raw_values=body.raw_values,
    )
    job_type, payload = export_job_payload(request)
    return _enqueue_document(session, user_id, grid_key, job_type, payload)


@router.post("/grids/{grid_key}/print")
def post_grid_print(
    grid_key: str,
    body: GridPrintBody,
    session: _SessionDep,
    user_id: _UserDep,
    catalog: _CatalogDep,
) -> Envelope:
    """Enqueue a grid print document: same scope rule as export, no format
    choice — the print document renders formatted values by definition
    (REQ-027). Answers ``data.jobId`` exactly as export does (DB-S11)."""
    view = _live_grid_and_view(session, grid_key, body.view_id)
    entity_type, _ = _view_entity(session, view, catalog)
    request = _document_request(session, view, entity_type, body)
    job_type, payload = print_job_payload(request)
    return _enqueue_document(session, user_id, grid_key, job_type, payload)
