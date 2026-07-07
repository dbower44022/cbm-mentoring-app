"""``/panels`` — the area grid surface over the seeded mentor sources (WTK-233).

What the frontend's universal grid panel consumes for the REQ-071 areas:

- ``GET /panels/{panelKey}/grid`` — the panel view-model: title, the views
  the CALLER'S roles may read (view keys are the seeded data-source keys, so
  the client's mentoring actions and the engagement preview activate by
  matching them), the active view (the engagements panel's is REQ-072's "My
  Active Engagements" — the mentor landing view), and the displayed columns.
- ``GET /panels/{panelKey}/rows`` — the view's rows through
  :func:`~mentorapp.access.grants.run_stored_data_source`: authorization
  precedes the load (an ungranted caller gets the standard 403 before any
  read), and the REQ-019 row filter binds the session user server-side, so
  mentor isolation here is the same isolation every other consumer gets.
  Live search (three characters and up, REQ-020) and header sorts apply on
  top of the source's own projection.
- ``GET /panels/{panelKey}/aggregates`` — whole-set counts for the status
  bar: the filtered total plus the un-narrowed total (the "N rows hidden by
  search" gap), issued by the client in parallel with the rows.

Why this is NOT the ``/grids`` surface: that router serves admin-authored
``grid``/``gridView`` records over ENTITY-backed sources through the ORM
engines. The area sources are admin-SQL sources (stored SELECTs over the
generated views with server-side user scoping) — they have no entity class
to hand the list engine, so their rows come from the one validated admin-SQL
path and the narrowing (search/sort) folds in Python over the bounded result.
The sources are per-user working sets, so an unpaged answer is honest today;
``nextCursor`` is served (always null) to keep the wire shape stable for the
day a source outgrows one page.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from mentorapp.access.grants import (
    StoredGrantRegistry,
    authorize_data_source,
    run_stored_data_source,
)
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.envelope import Envelope, field_error, ok
from mentorapp.api.errors import ApiValidationError, RecordNotFoundError
from mentorapp.api.panel_catalog import (
    PANELS_BY_KEY,
    PanelSpec,
    PanelViewSpec,
    column_label,
    granted_views,
)

# The one role seam, deliberately imported (the mentoring-router stance).
from mentorapp.api.routers.workprocess import RoleSource, get_role_source
from mentorapp.observability import get_logger

log = get_logger(__name__)

router = APIRouter()

CODE_UNKNOWN_PANEL_VIEW = "unknownPanelView"
CODE_UNKNOWN_SORT_FIELD = "unknownSortField"

_PANEL_ENTITY = "panel"

# REQ-020: live search arms at three characters — the grid standard's one
# threshold, restated here because this surface narrows in Python.
_MIN_SEARCH_LENGTH = 3

_SessionDep = Annotated[Session, Depends(get_session)]
_UserDep = Annotated[uuid.UUID, Depends(get_current_user_id)]
_RolesDep = Annotated[RoleSource, Depends(get_role_source)]


def _live_panel(panel_key: str) -> PanelSpec:
    spec = PANELS_BY_KEY.get(panel_key)
    if spec is None:
        raise RecordNotFoundError(_PANEL_ENTITY, panel_key)
    return spec


def _panel_view(panel: PanelSpec, view_key: str | None) -> PanelViewSpec:
    """The addressed view of this panel; None means the panel's first view."""
    if view_key is None:
        return panel.views[0]
    for view in panel.views:
        if view.view_key == view_key:
            return view
    raise ApiValidationError(
        [
            field_error(
                "view",
                CODE_UNKNOWN_PANEL_VIEW,
                f"'{view_key}' is not a view of the {panel.title} panel; its "
                f"views are {', '.join(v.view_key for v in panel.views)}.",
            )
        ]
    )


def _cell(value: Any) -> Any:
    # Admin-SQL rows carry driver types (UUIDs, datetimes); the wire carries
    # JSON scalars. Numbers pass through; everything else becomes its string
    # form — the same rendering the grid would apply anyway.
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _search_narrow(
    rows: list[dict[str, Any]], columns: tuple[str, ...], search: str
) -> tuple[list[dict[str, Any]], bool]:
    """REQ-020's rule: search layers ON TOP of the source's own filters.

    Case-insensitive substring across the DISPLAYED columns only; text under
    the arming threshold never ran a search.
    """
    needle = search.strip().lower()
    if len(needle) < _MIN_SEARCH_LENGTH:
        return rows, False
    matched = [
        row
        for row in rows
        if any(
            needle in str(row.get(column, "")).lower()
            for column in columns
            if row.get(column) is not None
        )
    ]
    return matched, True


def _sorted_rows(
    rows: list[dict[str, Any]], columns: tuple[str, ...], sort: str
) -> list[dict[str, Any]]:
    """Apply the client's header sorts (``field:asc,field:desc``) stably.

    Keys apply lowest priority first (stable sorts compose right-to-left);
    None sorts last regardless of direction, matching the triage read's
    NULLs-last stance. An unknown field is the per-field 422.
    """
    if not sort:
        return rows
    keys: list[tuple[str, bool]] = []
    for part in sort.split(","):
        field_name, _, direction = part.strip().partition(":")
        if field_name not in columns:
            raise ApiValidationError(
                [
                    field_error(
                        "sort",
                        CODE_UNKNOWN_SORT_FIELD,
                        f"'{field_name}' is not a displayed column of this view.",
                    )
                ]
            )
        keys.append((field_name, direction == "desc"))
    ordered = list(rows)
    for field_name, descending in reversed(keys):

        def sort_key(row: dict[str, Any], _field: str = field_name) -> tuple[bool, Any]:
            value = row.get(_field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return (value is None, value)
            return (value is None, str(value) if value is not None else "")

        ordered.sort(key=sort_key, reverse=descending)
    return ordered


def _view_rows(
    session: Session,
    user_id: uuid.UUID,
    roles: RoleSource,
    view: PanelViewSpec,
) -> list[dict[str, Any]]:
    """The view's rows through the grant-enforced stored-source path."""
    return run_stored_data_source(
        session,
        view.data_source_key,
        user_id=user_id,
        user_roles=roles.user_roles(user_id),
    )


@router.get("/panels/{panel_key}/grid")
def get_panel_grid(
    panel_key: str, session: _SessionDep, user_id: _UserDep, roles: _RolesDep
) -> Envelope:
    """The panel view-model the universal grid boots from (WTK-233).

    ``data.views`` lists only the views the caller's roles cover, each
    naming its ``dataSourceKey`` (the seeded key — what activates the
    client-side mentoring actions and domain preview). ``data.activeViewKey``
    is the first granted view: for the engagements panel that is REQ-072's
    "My Active Engagements", the mentor landing view. 404 for a panel that
    does not exist; 403 through the one grant boundary when no view is
    granted (opening an area you were not shown is audit-relevant, not a
    404 — panels are product surface, not probeable records).
    """
    panel = _live_panel(panel_key)
    views = granted_views(
        panel,
        grants=StoredGrantRegistry(session),
        user_roles=roles.user_roles(user_id),
    )
    if not views:
        # The attempt form of the decision (opening an area the user was not
        # shown is audit-relevant): raises DataSourceAccessError, which the
        # handler maps to the standard 403 envelope.
        authorize_data_source(
            StoredGrantRegistry(session),
            data_source_key=panel.primary_source_key,
            user_id=user_id,
            user_roles=roles.user_roles(user_id),
        )
    active = views[0]
    return ok(
        data={
            "gridId": panel.panel_key,
            "title": panel.title,
            "views": [
                {
                    "viewKey": view.view_key,
                    "label": view.label,
                    "criteria": view.criteria,
                    "dataSourceKey": view.data_source_key,
                    "isSystemView": True,
                    "allowAdHocFilters": False,
                }
                for view in views
            ],
            "activeViewKey": active.view_key,
            "columns": [
                {"fieldName": name, "label": column_label(name)} for name in active.columns
            ],
            # The mentoring domain actions join CLIENT-side keyed by data
            # source (the pass-2 design); the panel itself declares none.
            "actions": [],
            "commonActionKeys": [],
            "recentSearches": [],
        }
    )


@router.get("/panels/{panel_key}/rows")
def get_panel_rows(
    panel_key: str,
    session: _SessionDep,
    user_id: _UserDep,
    roles: _RolesDep,
    view: str | None = None,
    search: str = "",
    sort: str = "",
    cursor: str | None = None,
) -> Envelope:
    """One view's rows: the stored source, narrowed by search, client-sorted.

    Authorization and REQ-019 user scoping happen inside the stored-source
    run — this endpoint never re-derives either. ``rows[].recordId`` and
    ``rows[].title`` come from the view's declared identity columns (falling
    back to the title, then a positional key, for sources whose id column is
    nullable). 404 unknown panel; 422 unknown view or sort field; 403 for an
    ungranted source.
    """
    panel = _live_panel(panel_key)
    view_spec = _panel_view(panel, view)
    rows = _view_rows(session, user_id, roles, view_spec)
    narrowed, search_applied = _search_narrow(rows, view_spec.columns, search)
    ordered = _sorted_rows(narrowed, view_spec.columns, sort)
    payload = []
    for index, row in enumerate(ordered):
        record_id = row.get(view_spec.record_id_field) or row.get(view_spec.title_field)
        payload.append(
            {
                "recordId": str(record_id) if record_id is not None else f"row-{index}",
                "title": str(row.get(view_spec.title_field) or ""),
                "values": {name: _cell(row.get(name)) for name in view_spec.columns},
            }
        )
    log.info(
        "panel rows served",
        extra={
            "context": {
                "userId": str(user_id),
                "panelKey": panel_key,
                "viewKey": view_spec.view_key,
                "rowCount": len(payload),
                "searchApplied": search_applied,
            }
        },
    )
    # nextCursor is part of the wire contract (DB-S8 shape) even while the
    # bounded per-user sources serve in one page — see the module docstring.
    return ok(
        data={"rows": payload, "nextCursor": None},
        meta={"searchApplied": search_applied},
    )


@router.get("/panels/{panel_key}/aggregates")
def get_panel_aggregates(
    panel_key: str,
    session: _SessionDep,
    user_id: _UserDep,
    roles: _RolesDep,
    view: str | None = None,
    search: str = "",
) -> Envelope:
    """Whole-set counts for the status bar (REQ-026's parallel read).

    ``totalCount`` is the filtered set (source + search); ``unnarrowedCount``
    is the same view WITHOUT the search — the client's "N rows hidden"
    honesty gap. No footer aggregates: the area views declare none (FND-019
    is a per-view declaration, and none of the seeded sources carries one).
    """
    panel = _live_panel(panel_key)
    view_spec = _panel_view(panel, view)
    rows = _view_rows(session, user_id, roles, view_spec)
    narrowed, _ = _search_narrow(rows, view_spec.columns, search)
    return ok(data={"totalCount": len(narrowed), "unnarrowedCount": len(rows), "footer": {}})
