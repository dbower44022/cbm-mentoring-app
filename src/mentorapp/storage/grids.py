"""Grid, view, and grid-state entities — the persisted grid data model (WTK-041).

Implements the storage surface behind the canonical list-view grid
(REQ-016..REQ-031). ``grid`` is one registered grid surface: its stable key
(the URL anchor, REQ-028), the action-bar/status-bar region configuration
(REQ-021, REQ-026), and the standard-locked table behaviors — infinite
scrolling, automatic column expansion, the one keyboard model (REQ-016,
REQ-024). ``gridView`` is one view a grid can show (REQ-017, REQ-018):
its data source, displayed fields with order/width/format, grouping, row
theme, filters, and the system/user split with read-only system views and
temporary-modified copies. ``sortSpec`` is one column of a view's sort order
(REQ-025). ``gridState`` carries the durable per-user-per-grid extras
(the remembered searches, REQ-020); ``gridSessionState`` the session-only
return-to-grid restore state (REQ-031); ``gridDeepLink`` a shareable link
naming a grid and view — a reference, never a grant (REQ-028).

Associations: ``gridLastUsedView`` is the (user, grid) → view record each
grid opens on (REQ-017); ``sortSpec.gridViewID`` orders a view's sorts (the
grid ↔ view ↔ sortSpec association); ``gridView.userID`` is the view-owner
association — null marks a system view, a live user row its owner.

These are platform tables (``StructuralColumnsMixin`` + ``Base``, not
``BaseEntity``): like ``dataSource`` and ``userPreference`` they are app
configuration, get no schema-registry rows and no generated read views, and
never surface through the admin read surface. Every foreign-key column
carries the exact name of the primary key it references (DB-R2b) — which is
why the last-used view is an association table, not a role-named column.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mentorapp.storage.base import Base, JsonValue, StructuralColumnsMixin, uuid7

# Same partial live-row predicate as models.py/auth.py (DB-S3, REQ-052).
_LIVE = text('"deletedAt" IS NULL')
# View-name uniqueness is split three ways: system views (no owner), a user's
# saved views, and temporary-modified copies (REQ-017) — a temp copy keeps its
# base view's name until saved, so it must never collide with the saved row.
_LIVE_SYSTEM_VIEW = text('"deletedAt" IS NULL AND "userID" IS NULL')
_LIVE_SAVED_USER_VIEW = text(
    '"deletedAt" IS NULL AND "userID" IS NOT NULL AND NOT "temporaryModifiedFlag"'
)

# REQ-017's view classes. App-validated vocabulary, never a database enum (DB-S7).
VIEW_TYPES: Final[tuple[str, ...]] = ("system", "user")

# REQ-025's sort directions, app-validated like VIEW_TYPES.
SORT_DIRECTIONS: Final[tuple[str, ...]] = ("ascending", "descending")


class Grid(StructuralColumnsMixin, Base):
    """One registered grid surface — three regions over one data set (REQ-016).

    The region structure (action bar / data table / status bar) is fixed by
    the standard; what varies per grid is the region *content* configuration.
    The behavior columns default to the standard-locked values (infinite
    scrolling, column expansion, the single keyboard model) so the contract
    is explicit in data and the API can refuse a drifting write — no grid
    ever gets a page size (REQ-016) or a private keyboard scheme (REQ-024).
    """

    __tablename__ = "grid"
    __table_args__ = (
        Index(
            "uq_grid_gridKey_live",
            "gridKey",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    grid_id: Mapped[uuid.UUID] = mapped_column("gridID", primary_key=True, default=uuid7)
    # The stable key a grid URL and deep link identify the grid by (REQ-028).
    grid_key: Mapped[str] = mapped_column("gridKey", String(200), nullable=False)
    grid_name: Mapped[str] = mapped_column("gridName", String(200), nullable=False)
    # Action-bar content (REQ-021): the two promoted common actions and the
    # Other Actions ordering; the search box and CRUD set are standard-fixed.
    action_bar_config: Mapped[dict[str, Any]] = mapped_column(
        "actionBarConfig", JsonValue, nullable=False, default=dict
    )
    # Status-bar content (REQ-026): which aggregates the footer row shows;
    # counts/progress placement is standard-fixed.
    status_bar_config: Mapped[dict[str, Any]] = mapped_column(
        "statusBarConfig", JsonValue, nullable=False, default=dict
    )
    infinite_scroll_flag: Mapped[bool] = mapped_column(
        "infiniteScrollFlag", nullable=False, default=True
    )
    column_expansion_flag: Mapped[bool] = mapped_column(
        "columnExpansionFlag", nullable=False, default=True
    )
    # REQ-024: one keyboard model covers every grid — "standard" is the only
    # sanctioned value; the column names the contract rather than hardcoding it.
    keyboard_model_key: Mapped[str] = mapped_column(
        "keyboardModelKey", String(50), nullable=False, default="standard"
    )

    views: Mapped[list[GridView]] = relationship(back_populates="grid")


class GridView(StructuralColumnsMixin, Base):
    """One view of a grid: data source, columns, grouping, theme (REQ-017, REQ-018).

    ``viewType`` (``VIEW_TYPES``) splits system from user views; ``userID``
    is the view-owner association — null on system views, which are read-only
    (REQ-017; ``readOnlyFlag`` also lets an admin lock a promoted view).
    A header sort or ad-hoc filter never mutates a shared view: it is saved
    as the acting user's copy with ``temporaryModifiedFlag`` set — what the
    selector marks, what REQ-031 restores, and what "save as my view" clears.
    ``displayedFields`` is the ordered column list (name, width, format);
    order is list order (REQ-018). ``viewFilters`` are the view's own filters
    that search and ad-hoc filters stack onto, never replace (REQ-020,
    REQ-029). ``adHocFilterFlag`` is REQ-029's per-view switch for column-
    header filters. Every displayed or filtered field must be one the view's
    data source exposes (REQ-019) — validated by the API, not the database.
    """

    __tablename__ = "gridView"
    __table_args__ = (
        Index(
            "uq_gridView_system_name_live",
            "gridID",
            "gridViewName",
            unique=True,
            sqlite_where=_LIVE_SYSTEM_VIEW,
            postgresql_where=_LIVE_SYSTEM_VIEW,
        ),
        Index(
            "uq_gridView_owner_name_live",
            "gridID",
            "userID",
            "gridViewName",
            unique=True,
            sqlite_where=_LIVE_SAVED_USER_VIEW,
            postgresql_where=_LIVE_SAVED_USER_VIEW,
        ),
        # The view-selector read (REQ-017): every live view of one grid. The
        # uniques above exclude temp copies, so neither can serve this scan.
        Index(
            "ix_gridView_gridID_live",
            "gridID",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    grid_view_id: Mapped[uuid.UUID] = mapped_column(
        "gridViewID", primary_key=True, default=uuid7
    )
    grid_id: Mapped[uuid.UUID] = mapped_column(
        "gridID", ForeignKey("grid.gridID"), nullable=False
    )
    # REQ-018: the first thing a view defines is its data source.
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        "dataSourceID", ForeignKey("dataSource.dataSourceID"), nullable=False
    )
    grid_view_name: Mapped[str] = mapped_column("gridViewName", String(200), nullable=False)
    view_type: Mapped[str] = mapped_column("viewType", String(50), nullable=False)
    # The view-owner association: null = system view (REQ-017).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        "userID", ForeignKey("appUser.userID"), default=None
    )
    read_only_flag: Mapped[bool] = mapped_column("readOnlyFlag", nullable=False, default=False)
    temporary_modified_flag: Mapped[bool] = mapped_column(
        "temporaryModifiedFlag", nullable=False, default=False
    )
    # Ordered list of {fieldName, columnWidth, columnFormat} (REQ-018).
    displayed_fields: Mapped[list[dict[str, Any]]] = mapped_column(
        "displayedFields", JsonValue, nullable=False, default=list
    )
    # {groupFields, treeFlag, startCollapsedFlag} or null = ungrouped (REQ-018).
    grouping_config: Mapped[dict[str, Any] | None] = mapped_column(
        "groupingConfig", JsonValue, default=None
    )
    # {rowHeight, colors, font} or null = the standard theme (REQ-018).
    row_theme: Mapped[dict[str, Any] | None] = mapped_column(
        "rowTheme", JsonValue, default=None
    )
    view_filters: Mapped[dict[str, Any] | None] = mapped_column(
        "viewFilters", JsonValue, default=None
    )
    ad_hoc_filter_flag: Mapped[bool] = mapped_column(
        "adHocFilterFlag", nullable=False, default=True
    )

    grid: Mapped[Grid] = relationship(back_populates="views")
    sort_specs: Mapped[list[SortSpec]] = relationship(
        back_populates="grid_view", order_by="SortSpec.sort_position"
    )


class SortSpec(StructuralColumnsMixin, Base):
    """One column of a view's sort order (REQ-025).

    ``sortPosition`` is 1-based: 1 is the sole/primary sort, 2 and 3 the
    shift-click secondaries — the numbered badge the header shows. Both
    uniques are per-view: one position per slot, one spec per column.
    ``sortFieldName`` must be a field the view's data source exposes,
    API-validated like the displayed fields (REQ-019).
    """

    __tablename__ = "sortSpec"
    __table_args__ = (
        Index(
            "uq_sortSpec_view_position_live",
            "gridViewID",
            "sortPosition",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
        Index(
            "uq_sortSpec_view_field_live",
            "gridViewID",
            "sortFieldName",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    sort_spec_id: Mapped[uuid.UUID] = mapped_column(
        "sortSpecID", primary_key=True, default=uuid7
    )
    grid_view_id: Mapped[uuid.UUID] = mapped_column(
        "gridViewID", ForeignKey("gridView.gridViewID"), nullable=False
    )
    sort_field_name: Mapped[str] = mapped_column("sortFieldName", String(100), nullable=False)
    # ``SORT_DIRECTIONS`` vocabulary, app-validated (DB-S7).
    sort_direction: Mapped[str] = mapped_column("sortDirection", String(20), nullable=False)
    sort_position: Mapped[int] = mapped_column("sortPosition", nullable=False)

    grid_view: Mapped[GridView] = relationship(back_populates="sort_specs")


class GridLastUsedView(StructuralColumnsMixin, Base):
    """The (user, grid) → view association each grid opens on (REQ-017).

    Long-term persistence per REQ-031 ("view choice persists long-term") and
    the fallback target when a deep link names a view the recipient cannot
    read (REQ-028). An association table rather than a column so the foreign
    key keeps the referenced key's exact name (DB-R2b).
    """

    __tablename__ = "gridLastUsedView"
    __table_args__ = (
        Index(
            "uq_gridLastUsedView_user_grid_live",
            "userID",
            "gridID",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    grid_last_used_view_id: Mapped[uuid.UUID] = mapped_column(
        "gridLastUsedViewID", primary_key=True, default=uuid7
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        "userID", ForeignKey("appUser.userID"), nullable=False
    )
    grid_id: Mapped[uuid.UUID] = mapped_column(
        "gridID", ForeignKey("grid.gridID"), nullable=False
    )
    grid_view_id: Mapped[uuid.UUID] = mapped_column(
        "gridViewID", ForeignKey("gridView.gridViewID"), nullable=False
    )


class GridState(StructuralColumnsMixin, Base):
    """Durable per-user-per-grid state beyond the view choice (REQ-020).

    Today that is the remembered searches — the last five per grid, newest
    first, trimmed by the API on write. Kept apart from ``gridSessionState``
    because this survives the session and apart from ``gridLastUsedView``
    because that association is read on every grid open (REQ-017) and this
    is not.
    """

    __tablename__ = "gridState"
    __table_args__ = (
        Index(
            "uq_gridState_user_grid_live",
            "userID",
            "gridID",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    grid_state_id: Mapped[uuid.UUID] = mapped_column(
        "gridStateID", primary_key=True, default=uuid7
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        "userID", ForeignKey("appUser.userID"), nullable=False
    )
    grid_id: Mapped[uuid.UUID] = mapped_column(
        "gridID", ForeignKey("grid.gridID"), nullable=False
    )
    recent_searches: Mapped[list[str]] = mapped_column(
        "recentSearches", JsonValue, nullable=False, default=list
    )


class GridSessionState(StructuralColumnsMixin, Base):
    """Session-only return-to-grid restore state (REQ-031).

    One row per (session, grid): the active view — a temporary-modified copy
    when one is in play — plus search text, scroll position, selection, and
    focused row, restored exactly while the data refreshes underneath.
    Session-scoped by the REQ-031 rule ("the rest of the restored state is
    session-only"): rows die with their ``authSession``. Record identifiers
    are data-source values, stored as text soft references like
    ``appUser.crmUserID`` — a data source may expose CRM records that have
    no local row to foreign-key.
    """

    __tablename__ = "gridSessionState"
    __table_args__ = (
        Index(
            "uq_gridSessionState_session_grid_live",
            "authSessionID",
            "gridID",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    grid_session_state_id: Mapped[uuid.UUID] = mapped_column(
        "gridSessionStateID", primary_key=True, default=uuid7
    )
    auth_session_id: Mapped[uuid.UUID] = mapped_column(
        "authSessionID", ForeignKey("authSession.authSessionID"), nullable=False
    )
    grid_id: Mapped[uuid.UUID] = mapped_column(
        "gridID", ForeignKey("grid.gridID"), nullable=False
    )
    # The active view, including a temporary-modified copy; null = the grid
    # has not resolved a view for this session yet (REQ-017 fallback order).
    grid_view_id: Mapped[uuid.UUID | None] = mapped_column(
        "gridViewID", ForeignKey("gridView.gridViewID"), default=None
    )
    search_text: Mapped[str | None] = mapped_column("searchText", String(500), default=None)
    # First visible row index into the filtered set — enough for infinite
    # scrolling to reload up to the same window (REQ-016, REQ-031).
    scroll_position: Mapped[int] = mapped_column("scrollPosition", nullable=False, default=0)
    selected_record_ids: Mapped[list[str]] = mapped_column(
        "selectedRecordIDs", JsonValue, nullable=False, default=list
    )
    focused_record_id: Mapped[str | None] = mapped_column(
        "focusedRecordID", String(100), default=None
    )


class GridDeepLink(StructuralColumnsMixin, Base):
    """A shareable URL token naming a grid and its active view (REQ-028).

    A link is a reference, never a grant: opening one still requires
    data-source permission, and a link to a view the recipient cannot read
    falls back to their last-used view with a notice — resolution logic is
    the API's; this row only records what the link names. Null ``gridViewID``
    links the grid itself, resolved to the opener's last-used view.
    """

    __tablename__ = "gridDeepLink"
    __table_args__ = (
        Index(
            "uq_gridDeepLink_deepLinkKey_live",
            "deepLinkKey",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    grid_deep_link_id: Mapped[uuid.UUID] = mapped_column(
        "gridDeepLinkID", primary_key=True, default=uuid7
    )
    # The token the URL carries — what identifies the grid and view (REQ-028).
    deep_link_key: Mapped[str] = mapped_column("deepLinkKey", String(200), nullable=False)
    grid_id: Mapped[uuid.UUID] = mapped_column(
        "gridID", ForeignKey("grid.gridID"), nullable=False
    )
    grid_view_id: Mapped[uuid.UUID | None] = mapped_column(
        "gridViewID", ForeignKey("gridView.gridViewID"), default=None
    )
