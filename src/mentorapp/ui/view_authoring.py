"""View & data-source authoring processes (WTK-049): the REQ-017..019 lifecycle verbs.

The process layer between the WTK-041 tables (``storage.grids``,
``storage.auth.DataSource``), the WTK-044 permission rules
(``access.views``), and the WTK-043 confirmation component — each verb is
one authorized storage mutation the API and shell invoke, never a second
home for the rules it composes:

- **create_view** (REQ-018): the walkthrough's final step —
  :data:`CREATE_VIEW_WALKTHROUGH` fixes the step order (data source →
  displayed fields → grouping → row theme) and :func:`create_view` validates
  the finished :class:`ViewDraft` against the source's exposed fields
  (REQ-019's "every displayed field is one the source exposes") before
  inserting the saved user view.
- **save_as_user_view / apply_temporary_view** (REQ-017):
  :func:`save_as_user_view` lands where
  :func:`~mentorapp.access.views.save_disposition` says — in place only on
  the user's own saved unlocked view, otherwise a new user view; a saved
  temporary copy is superseded (soft-deleted). :func:`apply_temporary_view`
  materializes the session's working copy (``temporaryModifiedFlag``), one
  per user per grid, reused across modifications.
- **restore_last_used_view** (REQ-017/REQ-031): reads the ONE long-term
  piece of grid state — the ``userPreference`` row under
  ``grid_surface.last_view_preference_key`` — written by
  :func:`remember_last_used_view`; a stale or invisible remembered view
  falls back to the grid's default system view with an educate notice,
  mirroring ``grid_surface.resolve_grid_link``'s fallback semantics.
- **promote_view_to_system** (REQ-017): the one sharing path — an admin
  COPIES a saved user view into a read-only system view; the owner's
  original is never touched. Gated by
  :func:`~mentorapp.access.views.authorize_view_promotion`.
- **author_data_source** (REQ-019): both authoring modes behind
  :func:`~mentorapp.access.views.authorize_data_source_authoring` — raw SQL,
  or the visual builder (:class:`VisualQuery`: joins + calculated fields)
  compiled by :func:`compile_visual_query` into the ONE executable form;
  either way ``adminsql.validate_admin_sql`` is the gate before the row
  persists, and user scoping stays the server-bound ``:currentUserID``
  contract.
- **delete_user_view** (REQ-021/REQ-022): the lifecycle's destructive step,
  confirmed through the ONE shared component —
  :func:`delete_view_confirmation` is ``grid_panel.destructive_confirmation``
  (exact count, sample records, hidden-selected callout, soft-delete-honest
  wording), never a private dialog.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.access.views import (
    SAVE_IN_PLACE,
    CapabilityLookup,
    ViewFacts,
    ViewPermissionError,
    authorize_data_source_authoring,
    authorize_view_management,
    authorize_view_promotion,
    can_apply_temporarily,
    save_disposition,
)
from mentorapp.api.grid_surface import last_view_preference_key
from mentorapp.observability import get_logger
from mentorapp.storage import DataSource, Grid, GridView, UserPreference, utcnow
from mentorapp.storage.adminsql import AdminSqlSource, validate_admin_sql
from mentorapp.ui.auth_flows import EducateMessage
from mentorapp.ui.grid_panel import ActionConfirmation, destructive_confirmation
from mentorapp.ui.record_preview import PanelAction

log = get_logger(__name__)


class ViewAuthoringError(ValueError):
    """An authoring input rejected before any write, with the reason."""


# --- The create-view walkthrough (REQ-018) ------------------------------------------

# The stepped order the shell renders; ``create_view`` is the final commit.
CREATE_VIEW_WALKTHROUGH: Final[tuple[str, ...]] = (
    "dataSource",
    "displayedFields",
    "grouping",
    "rowTheme",
)


@dataclass(frozen=True)
class ViewDraft:
    """The finished walkthrough, as plain values the commit step validates.

    ``displayed_fields`` is the ordered ``{fieldName, columnWidth,
    columnFormat}`` list ``gridView.displayedFields`` persists;
    ``grouping_config`` the ``{groupFields, treeFlag, startCollapsedFlag}``
    document or ``None`` for ungrouped; ``row_theme`` ``None`` means the
    standard theme (REQ-018).
    """

    grid_view_name: str
    data_source_id: uuid.UUID
    displayed_fields: tuple[dict[str, Any], ...]
    grouping_config: dict[str, Any] | None = None
    row_theme: dict[str, Any] | None = None
    view_filters: dict[str, Any] | None = None
    ad_hoc_filter_flag: bool = True
    view_aggregates: tuple[dict[str, Any], ...] = ()


def _referenced_field_names(
    displayed_fields: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    grouping_config: dict[str, Any] | None,
) -> list[str]:
    names = [str(spec["fieldName"]) for spec in displayed_fields]
    if grouping_config is not None:
        names.extend(str(name) for name in grouping_config.get("groupFields", []))
    return names


def _validate_against_source(
    source: DataSource,
    displayed_fields: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    grouping_config: dict[str, Any] | None,
) -> None:
    """REQ-019's bound: a view may only display/group what its source exposes."""
    if not displayed_fields:
        raise ViewAuthoringError("a view must display at least one field")
    exposed = set(source.exposed_fields)
    unknown = [
        name
        for name in _referenced_field_names(displayed_fields, grouping_config)
        if name not in exposed
    ]
    if unknown:
        raise ViewAuthoringError(
            f"fields not exposed by data source '{source.data_source_key}': "
            f"{sorted(set(unknown))}"
        )


def _live_source(session: Session, data_source_id: uuid.UUID) -> DataSource:
    source = session.get(DataSource, data_source_id)
    if source is None or source.deleted_at is not None:
        raise ViewAuthoringError(f"data source {data_source_id} does not exist")
    return source


def _saved_name_taken(
    session: Session, grid_id: uuid.UUID, name: str, owner_id: uuid.UUID | None
) -> bool:
    """Whether a LIVE saved view already holds this name in its uniqueness class.

    Pre-checked (rather than letting the partial unique index raise) so the
    caller gets an educate-worthy reason, not an IntegrityError.
    """
    query = select(GridView.grid_view_id).where(
        GridView.grid_id == grid_id,
        GridView.grid_view_name == name,
        GridView.deleted_at.is_(None),
        GridView.temporary_modified_flag.is_(False),
    )
    query = (
        query.where(GridView.user_id.is_(None))
        if owner_id is None
        else query.where(GridView.user_id == owner_id)
    )
    return session.scalars(query).first() is not None


def _view_facts(view: GridView) -> ViewFacts:
    """The access-rule projection of a stored view — the one translation site."""
    return ViewFacts(
        view_id=view.grid_view_id,
        owner_id=view.user_id,
        read_only=view.read_only_flag,
        temporary_modified=view.temporary_modified_flag,
    )


def create_view(
    session: Session, *, grid: Grid, draft: ViewDraft, user_id: uuid.UUID
) -> GridView:
    """Commit the create-view walkthrough as the user's saved view (REQ-018).

    Validates the draft against its data source's exposed fields and the
    live-name uniqueness class, then inserts a ``user`` view owned by the
    caller. Raises :class:`ViewAuthoringError` on any rejected input.
    """
    source = _live_source(session, draft.data_source_id)
    _validate_against_source(source, draft.displayed_fields, draft.grouping_config)
    if _saved_name_taken(session, grid.grid_id, draft.grid_view_name, user_id):
        raise ViewAuthoringError(
            f"you already have a view named '{draft.grid_view_name}' on this grid"
        )
    view = GridView(
        grid_id=grid.grid_id,
        data_source_id=source.data_source_id,
        grid_view_name=draft.grid_view_name,
        view_type="user",
        user_id=user_id,
        displayed_fields=list(draft.displayed_fields),
        grouping_config=draft.grouping_config,
        row_theme=draft.row_theme,
        view_filters=draft.view_filters,
        ad_hoc_filter_flag=draft.ad_hoc_filter_flag,
        view_aggregates=list(draft.view_aggregates) or None,
        created_by=user_id,
        modified_by=user_id,
    )
    session.add(view)
    session.flush()
    log.info(
        "view created",
        extra={
            "context": {
                "gridID": str(grid.grid_id),
                "gridViewID": str(view.grid_view_id),
                "userID": str(user_id),
            }
        },
    )
    return view


# --- Save-as-user & apply-temporarily (REQ-017) -------------------------------------

# The settings a temporary application may override — exactly the view-defined
# presentation columns; identity (grid, name, owner, type) never overrides.
TEMPORARY_OVERRIDE_FIELDS: Final[tuple[str, ...]] = (
    "displayed_fields",
    "grouping_config",
    "row_theme",
    "view_filters",
    "ad_hoc_filter_flag",
    "view_aggregates",
)


def _copy_settings(target: GridView, origin: GridView) -> None:
    for name in TEMPORARY_OVERRIDE_FIELDS:
        setattr(target, name, getattr(origin, name))


def save_as_user_view(
    session: Session, *, view: GridView, name: str, user_id: uuid.UUID
) -> GridView:
    """Save the given view's settings as the user's own (REQ-017).

    Lands where :func:`save_disposition` rules: in place (a rename) only on
    the user's own saved unlocked view; a system view, a locked view, or the
    session's temporary-modified copy becomes a NEW saved user view — the
    original is never touched. Saving a temporary copy soft-deletes it: the
    saved view IS those settings now, which is what clears the selector's
    modified flag.
    """
    if save_disposition(_view_facts(view), user_id=user_id) == SAVE_IN_PLACE:
        if name != view.grid_view_name and _saved_name_taken(
            session, view.grid_id, name, user_id
        ):
            raise ViewAuthoringError(f"you already have a view named '{name}' on this grid")
        view.grid_view_name = name
        view.modified_by = user_id
        session.flush()
        return view
    if _saved_name_taken(session, view.grid_id, name, user_id):
        raise ViewAuthoringError(f"you already have a view named '{name}' on this grid")
    saved = GridView(
        grid_id=view.grid_id,
        data_source_id=view.data_source_id,
        grid_view_name=name,
        view_type="user",
        user_id=user_id,
        created_by=user_id,
        modified_by=user_id,
    )
    _copy_settings(saved, view)
    session.add(saved)
    if view.temporary_modified_flag and view.user_id == user_id:
        view.deleted_at = utcnow()
        view.deleted_by = user_id
        view.modified_by = user_id
    session.flush()
    log.info(
        "view saved as user view",
        extra={
            "context": {
                "fromViewID": str(view.grid_view_id),
                "gridViewID": str(saved.grid_view_id),
                "userID": str(user_id),
            }
        },
    )
    return saved


def apply_temporary_view(
    session: Session,
    *,
    base_view: GridView,
    user_id: uuid.UUID,
    overrides: dict[str, Any],
) -> GridView:
    """Materialize the user's session-scoped working copy of a view (REQ-017).

    Open on anything the user can see — it touches nothing shared. One
    working copy per user per grid: a second modification updates the same
    row (re-seeded from the newly chosen base), matching the selector rule
    that choosing another view replaces, not stacks, temporary state.
    ``overrides`` keys must be :data:`TEMPORARY_OVERRIDE_FIELDS`.
    """
    facts = _view_facts(base_view)
    if not can_apply_temporarily(facts, user_id=user_id):
        log.info(
            "temporary view application refused",
            extra={
                "context": {
                    "viewID": str(base_view.grid_view_id),
                    "userID": str(user_id),
                }
            },
        )
        raise ViewPermissionError("applyTemporary", base_view.grid_view_id, user_id)
    unknown = sorted(set(overrides) - set(TEMPORARY_OVERRIDE_FIELDS))
    if unknown:
        raise ViewAuthoringError(f"unknown temporary override fields: {unknown}")
    displayed = overrides.get("displayed_fields", base_view.displayed_fields)
    grouping = overrides.get("grouping_config", base_view.grouping_config)
    _validate_against_source(
        _live_source(session, base_view.data_source_id), displayed, grouping
    )
    copy = session.scalars(
        select(GridView).where(
            GridView.grid_id == base_view.grid_id,
            GridView.user_id == user_id,
            GridView.temporary_modified_flag.is_(True),
            GridView.deleted_at.is_(None),
        )
    ).first()
    if copy is None:
        copy = GridView(
            grid_id=base_view.grid_id,
            view_type="user",
            user_id=user_id,
            temporary_modified_flag=True,
            created_by=user_id,
        )
        session.add(copy)
    # The temp copy keeps its base view's name (the storage uniques exclude
    # temp copies precisely so this never collides with the saved row).
    copy.grid_view_name = base_view.grid_view_name
    copy.data_source_id = base_view.data_source_id
    _copy_settings(copy, base_view)
    for name, value in overrides.items():
        setattr(copy, name, value)
    copy.modified_by = user_id
    session.flush()
    return copy


# --- Last-used view: remember & restore (REQ-017, REQ-031) --------------------------


def remember_last_used_view(
    session: Session, *, grid: Grid, view: GridView, user_id: uuid.UUID
) -> None:
    """Persist the ONE long-term piece of grid state through the preference row.

    Same key the deep-link fallback reads (``last_view_preference_key``,
    FND-018/DB-S13) — never a table or column.
    """
    key = last_view_preference_key(grid.grid_key)
    row = session.scalars(
        select(UserPreference).where(
            UserPreference.preference_key == key,
            UserPreference.user_id == user_id,
            UserPreference.deleted_at.is_(None),
        )
    ).first()
    value = {"gridViewID": str(view.grid_view_id)}
    if row is None:
        session.add(
            UserPreference(
                user_id=user_id,
                preference_key=key,
                preference_value=value,
                created_by=user_id,
                modified_by=user_id,
            )
        )
    elif row.preference_value != value:
        row.preference_value = value
        row.modified_by = user_id
    session.flush()


@dataclass(frozen=True)
class RestoredView:
    """What a grid opens on: the resolved view, and the notice when it fell back.

    ``view`` ``None`` means the grid has no live system view to fall back to
    — a configuration gap the notice explains rather than a blank grid.
    """

    view: GridView | None
    notice: EducateMessage | None = None


def _default_system_view(session: Session, grid: Grid) -> GridView | None:
    return session.scalars(
        select(GridView)
        .where(
            GridView.grid_id == grid.grid_id,
            GridView.user_id.is_(None),
            GridView.deleted_at.is_(None),
        )
        .order_by(GridView.created_at, GridView.grid_view_id)
    ).first()


def restore_last_used_view(session: Session, *, grid: Grid, user_id: uuid.UUID) -> RestoredView:
    """The view a grid opens on for THIS user (REQ-017: always the last displayed).

    An unset preference lands quietly on the grid's default system view; a
    remembered view that is gone, moved, or not the user's to see falls back
    the same way WITH the educate notice — never a blank grid, never silence
    about lost state.
    """
    key = last_view_preference_key(grid.grid_key)
    row = session.scalars(
        select(UserPreference).where(
            UserPreference.preference_key == key,
            UserPreference.user_id == user_id,
            UserPreference.deleted_at.is_(None),
        )
    ).first()
    if row is not None:
        remembered = session.get(GridView, uuid.UUID(row.preference_value["gridViewID"]))
        usable = (
            remembered is not None
            and remembered.deleted_at is None
            and remembered.grid_id == grid.grid_id
            and (remembered.user_id is None or remembered.user_id == user_id)
        )
        if usable:
            return RestoredView(remembered)
    fallback = _default_system_view(session, grid)
    if fallback is None:
        return RestoredView(
            None,
            EducateMessage(
                what_happened="This grid has no view to open.",
                why=f"'{grid.grid_name}' has no system view defined yet.",
                what_next="Ask a system administrator to add one — grids "
                "always open through a view.",
            ),
        )
    if row is None:
        return RestoredView(fallback)
    return RestoredView(
        fallback,
        EducateMessage(
            what_happened=f"Your last-used view isn't available, so '{grid.grid_name}' "
            f"opened on '{fallback.grid_view_name}'.",
            why="The view you last displayed here was removed or is no longer yours to see.",
            what_next="Pick another view from the selector — it becomes your "
            "new last-used view.",
        ),
    )


# --- Promotion: the one sharing path (REQ-017) --------------------------------------


def promote_view_to_system(
    session: Session, *, view: GridView, lookup: CapabilityLookup, user_id: uuid.UUID
) -> GridView:
    """Copy a saved user view into a system view — the admin's sharing path.

    A COPY, deliberately: user views are never sharable user-to-user, so
    promotion must not move or mutate the owner's view. Gated by
    ``gridView.promote`` plus the saved-user-view subject rule
    (:func:`authorize_view_promotion`).
    """
    authorize_view_promotion(_view_facts(view), lookup=lookup, user_id=user_id)
    if _saved_name_taken(session, view.grid_id, view.grid_view_name, None):
        raise ViewAuthoringError(
            f"a system view named '{view.grid_view_name}' already exists on this grid"
        )
    promoted = GridView(
        grid_id=view.grid_id,
        data_source_id=view.data_source_id,
        grid_view_name=view.grid_view_name,
        view_type="system",
        user_id=None,
        created_by=user_id,
        modified_by=user_id,
    )
    _copy_settings(promoted, view)
    session.add(promoted)
    session.flush()
    log.info(
        "view promoted to system",
        extra={
            "context": {
                "fromViewID": str(view.grid_view_id),
                "gridViewID": str(promoted.grid_view_id),
                "userID": str(user_id),
            }
        },
    )
    return promoted


# --- Data-source authoring: visual builder + raw SQL (REQ-019) ----------------------

# Visual-builder join vocabulary, app-validated like every enum (DB-S7).
JOIN_TYPES: Final[tuple[str, ...]] = ("inner", "left")

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote(reference: str) -> str:
    """Quote a (possibly view-qualified) reference, rejecting non-identifiers.

    The identifier gate is what keeps the visual document from smuggling SQL
    through a field name; free-form text only exists in calculated-field
    expressions, which the whole-statement ``validate_admin_sql`` pass and
    the read-only execution role bound.
    """
    parts = reference.split(".")
    if not parts or any(_IDENTIFIER.match(part) is None for part in parts):
        raise ViewAuthoringError(f"invalid identifier in visual query: {reference!r}")
    return ".".join(f'"{part}"' for part in parts)


@dataclass(frozen=True)
class VisualJoin:
    """One builder join: another read view, matched on one field pair."""

    view_name: str
    left_field: str
    right_field: str
    join_type: str = "left"

    def __post_init__(self) -> None:
        if self.join_type not in JOIN_TYPES:
            raise ViewAuthoringError(f"unknown join type: {self.join_type!r}")


@dataclass(frozen=True)
class CalculatedField:
    """One builder calculated field: an aliased SQL expression."""

    field_name: str
    expression: str


@dataclass(frozen=True)
class VisualQuery:
    """The visual builder's document: base view, joins, fields, calculations.

    Persisted verbatim (``dataSource.visualQueryDefinition``) so the builder
    reopens what the author drew; the compiled SQL stays the one executable
    form (WTK-041 ruling).
    """

    base_view: str
    selected_fields: tuple[str, ...]
    joins: tuple[VisualJoin, ...] = ()
    calculated_fields: tuple[CalculatedField, ...] = ()

    def as_definition(self) -> dict[str, Any]:
        return {
            "baseView": self.base_view,
            "selectedFields": list(self.selected_fields),
            "joins": [
                {
                    "viewName": join.view_name,
                    "leftField": join.left_field,
                    "rightField": join.right_field,
                    "joinType": join.join_type,
                }
                for join in self.joins
            ],
            "calculatedFields": [
                {"fieldName": calc.field_name, "expression": calc.expression}
                for calc in self.calculated_fields
            ],
        }

    def exposed_field_names(self) -> list[str]:
        """The fields the compiled source exposes — what views may display."""
        names = [reference.split(".")[-1] for reference in self.selected_fields]
        names.extend(calc.field_name for calc in self.calculated_fields)
        return names


def compile_visual_query(query: VisualQuery, *, user_row_filter: str | None = None) -> str:
    """Compile the builder document into the one executable SELECT.

    ``user_row_filter`` names the column bound server-side to
    ``:currentUserID`` (DB-S9) — the builder injects the scoping clause so a
    scoped source can never be drawn without it.
    """
    if not query.selected_fields and not query.calculated_fields:
        raise ViewAuthoringError("a visual query must select at least one field")
    columns = [_quote(reference) for reference in query.selected_fields]
    for calc in query.calculated_fields:
        columns.append(f"({calc.expression}) AS {_quote(calc.field_name)}")
    sql = f"SELECT {', '.join(columns)} FROM {_quote(query.base_view)}"
    for join in query.joins:
        keyword = "JOIN" if join.join_type == "inner" else "LEFT JOIN"
        sql += (
            f" {keyword} {_quote(join.view_name)}"
            f" ON {_quote(join.left_field)} = {_quote(join.right_field)}"
        )
    if user_row_filter is not None:
        sql += f" WHERE {_quote(user_row_filter)} = :currentUserID"
    return sql


@dataclass(frozen=True)
class DataSourceDraft:
    """One authoring submission: exactly one of raw SQL or a visual document.

    Raw mode must declare ``exposed_fields`` (the SQL isn't parsed for them);
    visual mode derives them from the document. ``user_row_filter`` non-null
    declares the source user-scoped (DB-S9).
    """

    data_source_key: str
    data_source_name: str
    sql_text: str | None = None
    visual_query: VisualQuery | None = None
    exposed_fields: tuple[str, ...] = ()
    user_row_filter: str | None = None


def author_data_source(
    session: Session,
    *,
    lookup: CapabilityLookup,
    user_id: uuid.UUID,
    draft: DataSourceDraft,
) -> DataSource:
    """Create or update one admin-authored data source (REQ-019).

    Authoring-capability gated; both modes funnel through
    ``validate_admin_sql`` before anything persists, so a source that could
    be anything but one plain SELECT never reaches storage. Re-authoring the
    same live ``dataSourceKey`` updates it — one canonical row per key.
    """
    authorize_data_source_authoring(lookup, user_id=user_id)
    if (draft.sql_text is None) == (draft.visual_query is None):
        raise ViewAuthoringError("author exactly one of raw SQL or a visual query document")
    if draft.visual_query is not None:
        sql = compile_visual_query(draft.visual_query, user_row_filter=draft.user_row_filter)
        exposed = draft.visual_query.exposed_field_names()
        definition = draft.visual_query.as_definition()
    else:
        if not draft.exposed_fields:
            raise ViewAuthoringError("raw SQL sources must declare their exposed fields")
        sql = draft.sql_text
        exposed = list(draft.exposed_fields)
        definition = None
    validate_admin_sql(
        AdminSqlSource(
            data_source_key=draft.data_source_key,
            sql_text=sql,
            user_scoped_flag=draft.user_row_filter is not None,
        )
    )
    source = session.scalars(
        select(DataSource).where(
            DataSource.data_source_key == draft.data_source_key,
            DataSource.deleted_at.is_(None),
        )
    ).first()
    if source is None:
        source = DataSource(data_source_key=draft.data_source_key, created_by=user_id)
        session.add(source)
    source.data_source_name = draft.data_source_name
    source.data_source_sql = sql
    source.user_row_filter = draft.user_row_filter
    source.visual_query_definition = definition
    source.exposed_fields = exposed
    source.modified_by = user_id
    session.flush()
    log.info(
        "data source authored",
        extra={
            "context": {
                "dataSourceKey": draft.data_source_key,
                "visual": draft.visual_query is not None,
                "userScoped": draft.user_row_filter is not None,
                "userID": str(user_id),
            }
        },
    )
    return source


# --- The lifecycle's destructive step, through the ONE confirmation (REQ-022) -------

DELETE_VIEW_ACTION: Final = PanelAction(
    key="deleteView",
    label="Delete view",
    selection_contract="single",
    classification="destructive",
)


def delete_view_confirmation(view: GridView) -> ActionConfirmation:
    """The required confirmation before :func:`delete_user_view` runs.

    Built by the shared WTK-043 component, so it carries the standard's
    exact count, sample records, hidden-selected callout, and the
    soft-delete-honest recoverability wording by construction.
    """
    return destructive_confirmation(DELETE_VIEW_ACTION, (view.grid_view_name,))


def delete_user_view(session: Session, *, view: GridView, user_id: uuid.UUID) -> None:
    """Soft-delete the user's own saved view (REQ-017 manage-own, DB-S3).

    Authorization is :func:`authorize_view_management` — a system view, a
    locked view, or another user's view refuses with the logged
    :class:`ViewPermissionError`. Never a physical delete: the confirmation
    already told the user an administrator can restore it, and that must
    stay true.
    """
    authorize_view_management(_view_facts(view), user_id=user_id, action="delete")
    view.deleted_at = utcnow()
    view.deleted_by = user_id
    view.modified_by = user_id
    session.flush()
    log.info(
        "view deleted",
        extra={"context": {"gridViewID": str(view.grid_view_id), "userID": str(user_id)}},
    )
