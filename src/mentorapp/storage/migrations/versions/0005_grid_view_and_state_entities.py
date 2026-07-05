"""Grid, view, sort-spec, and grid-state entities (WTK-041, REQ-016..REQ-031).

Creates the grid platform tables: ``grid``, ``gridView``, ``sortSpec``, the
``gridLastUsedView`` association, ``gridState``, ``gridSessionState``, and
``gridDeepLink``; extends ``dataSource`` with the REQ-019 authoring columns
(``visualQueryDefinition``, ``exposedFields``). Same structural rules as
0001/0002: UUIDv7 app-generated keys (REQ-047), the eight structural system
columns (REQ-053), and partial live-row indexes (REQ-052). Platform tables —
no schema-registry rows and no read views.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# Mirrors storage.base.JsonValue: JSONB on Postgres, plain JSON on SQLite.
_JSON_OBJECT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_LIVE = sa.text('"deletedAt" IS NULL')
# The gridView name-uniqueness split (REQ-017): system views, saved user
# views; temporary-modified copies are excluded from uniqueness entirely.
_LIVE_SYSTEM_VIEW = sa.text('"deletedAt" IS NULL AND "userID" IS NULL')
_LIVE_SAVED_USER_VIEW = sa.text(
    '"deletedAt" IS NULL AND "userID" IS NOT NULL AND NOT "temporaryModifiedFlag"'
)


def _structural_columns() -> list[sa.Column]:
    return [
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdBy", sa.Uuid(), nullable=True),
        sa.Column("modifiedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modifiedBy", sa.Uuid(), nullable=True),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletedBy", sa.Uuid(), nullable=True),
        sa.Column("rowVersion", sa.Integer(), nullable=False),
        sa.Column("customAttributes", _JSON_OBJECT, nullable=False),
    ]


def upgrade() -> None:
    # REQ-019: the visual-builder document and the exposed-field list join the
    # existing raw-SQL columns; existing sources backfill to "raw SQL, nothing
    # exposed yet" — the server default exists only for that backfill.
    with op.batch_alter_table("dataSource", schema=None) as batch_op:
        batch_op.add_column(sa.Column("visualQueryDefinition", _JSON_OBJECT, nullable=True))
        batch_op.add_column(
            sa.Column(
                "exposedFields", _JSON_OBJECT, nullable=False, server_default=sa.text("'[]'")
            )
        )

    op.create_table(
        "grid",
        sa.Column("gridID", sa.Uuid(), nullable=False),
        sa.Column("gridKey", sa.String(length=200), nullable=False),
        sa.Column("gridName", sa.String(length=200), nullable=False),
        sa.Column("actionBarConfig", _JSON_OBJECT, nullable=False),
        sa.Column("statusBarConfig", _JSON_OBJECT, nullable=False),
        sa.Column("infiniteScrollFlag", sa.Boolean(), nullable=False),
        sa.Column("columnExpansionFlag", sa.Boolean(), nullable=False),
        sa.Column("keyboardModelKey", sa.String(length=50), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("gridID", name=op.f("pk_grid")),
    )
    with op.batch_alter_table("grid", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_grid_modifiedAt"), ["modifiedAt"], unique=False)
        batch_op.create_index(
            "uq_grid_gridKey_live",
            ["gridKey"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.create_table(
        "gridView",
        sa.Column("gridViewID", sa.Uuid(), nullable=False),
        sa.Column("gridID", sa.Uuid(), nullable=False),
        sa.Column("dataSourceID", sa.Uuid(), nullable=False),
        sa.Column("gridViewName", sa.String(length=200), nullable=False),
        sa.Column("viewType", sa.String(length=50), nullable=False),
        sa.Column("userID", sa.Uuid(), nullable=True),
        sa.Column("readOnlyFlag", sa.Boolean(), nullable=False),
        sa.Column("temporaryModifiedFlag", sa.Boolean(), nullable=False),
        sa.Column("displayedFields", _JSON_OBJECT, nullable=False),
        sa.Column("groupingConfig", _JSON_OBJECT, nullable=True),
        sa.Column("rowTheme", _JSON_OBJECT, nullable=True),
        sa.Column("viewFilters", _JSON_OBJECT, nullable=True),
        sa.Column("adHocFilterFlag", sa.Boolean(), nullable=False),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["gridID"], ["grid.gridID"], name=op.f("fk_gridView_gridID_grid")
        ),
        sa.ForeignKeyConstraint(
            ["dataSourceID"],
            ["dataSource.dataSourceID"],
            name=op.f("fk_gridView_dataSourceID_dataSource"),
        ),
        sa.ForeignKeyConstraint(
            ["userID"], ["appUser.userID"], name=op.f("fk_gridView_userID_appUser")
        ),
        sa.PrimaryKeyConstraint("gridViewID", name=op.f("pk_gridView")),
    )
    with op.batch_alter_table("gridView", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_gridView_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_gridView_system_name_live",
            ["gridID", "gridViewName"],
            unique=True,
            sqlite_where=_LIVE_SYSTEM_VIEW,
            postgresql_where=_LIVE_SYSTEM_VIEW,
        )
        batch_op.create_index(
            "uq_gridView_owner_name_live",
            ["gridID", "userID", "gridViewName"],
            unique=True,
            sqlite_where=_LIVE_SAVED_USER_VIEW,
            postgresql_where=_LIVE_SAVED_USER_VIEW,
        )
        batch_op.create_index(
            "ix_gridView_gridID_live",
            ["gridID"],
            unique=False,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.create_table(
        "sortSpec",
        sa.Column("sortSpecID", sa.Uuid(), nullable=False),
        sa.Column("gridViewID", sa.Uuid(), nullable=False),
        sa.Column("sortFieldName", sa.String(length=100), nullable=False),
        sa.Column("sortDirection", sa.String(length=20), nullable=False),
        sa.Column("sortPosition", sa.Integer(), nullable=False),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["gridViewID"],
            ["gridView.gridViewID"],
            name=op.f("fk_sortSpec_gridViewID_gridView"),
        ),
        sa.PrimaryKeyConstraint("sortSpecID", name=op.f("pk_sortSpec")),
    )
    with op.batch_alter_table("sortSpec", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_sortSpec_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_sortSpec_view_position_live",
            ["gridViewID", "sortPosition"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.create_index(
            "uq_sortSpec_view_field_live",
            ["gridViewID", "sortFieldName"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.create_table(
        "gridLastUsedView",
        sa.Column("gridLastUsedViewID", sa.Uuid(), nullable=False),
        sa.Column("userID", sa.Uuid(), nullable=False),
        sa.Column("gridID", sa.Uuid(), nullable=False),
        sa.Column("gridViewID", sa.Uuid(), nullable=False),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["userID"], ["appUser.userID"], name=op.f("fk_gridLastUsedView_userID_appUser")
        ),
        sa.ForeignKeyConstraint(
            ["gridID"], ["grid.gridID"], name=op.f("fk_gridLastUsedView_gridID_grid")
        ),
        sa.ForeignKeyConstraint(
            ["gridViewID"],
            ["gridView.gridViewID"],
            name=op.f("fk_gridLastUsedView_gridViewID_gridView"),
        ),
        sa.PrimaryKeyConstraint("gridLastUsedViewID", name=op.f("pk_gridLastUsedView")),
    )
    with op.batch_alter_table("gridLastUsedView", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_gridLastUsedView_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_gridLastUsedView_user_grid_live",
            ["userID", "gridID"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.create_table(
        "gridState",
        sa.Column("gridStateID", sa.Uuid(), nullable=False),
        sa.Column("userID", sa.Uuid(), nullable=False),
        sa.Column("gridID", sa.Uuid(), nullable=False),
        sa.Column("recentSearches", _JSON_OBJECT, nullable=False),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["userID"], ["appUser.userID"], name=op.f("fk_gridState_userID_appUser")
        ),
        sa.ForeignKeyConstraint(
            ["gridID"], ["grid.gridID"], name=op.f("fk_gridState_gridID_grid")
        ),
        sa.PrimaryKeyConstraint("gridStateID", name=op.f("pk_gridState")),
    )
    with op.batch_alter_table("gridState", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_gridState_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_gridState_user_grid_live",
            ["userID", "gridID"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.create_table(
        "gridSessionState",
        sa.Column("gridSessionStateID", sa.Uuid(), nullable=False),
        sa.Column("authSessionID", sa.Uuid(), nullable=False),
        sa.Column("gridID", sa.Uuid(), nullable=False),
        sa.Column("gridViewID", sa.Uuid(), nullable=True),
        sa.Column("searchText", sa.String(length=500), nullable=True),
        sa.Column("scrollPosition", sa.Integer(), nullable=False),
        sa.Column("selectedRecordIDs", _JSON_OBJECT, nullable=False),
        sa.Column("focusedRecordID", sa.String(length=100), nullable=True),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["authSessionID"],
            ["authSession.authSessionID"],
            name=op.f("fk_gridSessionState_authSessionID_authSession"),
        ),
        sa.ForeignKeyConstraint(
            ["gridID"], ["grid.gridID"], name=op.f("fk_gridSessionState_gridID_grid")
        ),
        sa.ForeignKeyConstraint(
            ["gridViewID"],
            ["gridView.gridViewID"],
            name=op.f("fk_gridSessionState_gridViewID_gridView"),
        ),
        sa.PrimaryKeyConstraint("gridSessionStateID", name=op.f("pk_gridSessionState")),
    )
    with op.batch_alter_table("gridSessionState", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_gridSessionState_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_gridSessionState_session_grid_live",
            ["authSessionID", "gridID"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.create_table(
        "gridDeepLink",
        sa.Column("gridDeepLinkID", sa.Uuid(), nullable=False),
        sa.Column("deepLinkKey", sa.String(length=200), nullable=False),
        sa.Column("gridID", sa.Uuid(), nullable=False),
        sa.Column("gridViewID", sa.Uuid(), nullable=True),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["gridID"], ["grid.gridID"], name=op.f("fk_gridDeepLink_gridID_grid")
        ),
        sa.ForeignKeyConstraint(
            ["gridViewID"],
            ["gridView.gridViewID"],
            name=op.f("fk_gridDeepLink_gridViewID_gridView"),
        ),
        sa.PrimaryKeyConstraint("gridDeepLinkID", name=op.f("pk_gridDeepLink")),
    )
    with op.batch_alter_table("gridDeepLink", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_gridDeepLink_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_gridDeepLink_deepLinkKey_live",
            ["deepLinkKey"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )


def downgrade() -> None:
    with op.batch_alter_table("gridDeepLink", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_gridDeepLink_deepLinkKey_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_gridDeepLink_modifiedAt"))
    op.drop_table("gridDeepLink")

    with op.batch_alter_table("gridSessionState", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_gridSessionState_session_grid_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_gridSessionState_modifiedAt"))
    op.drop_table("gridSessionState")

    with op.batch_alter_table("gridState", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_gridState_user_grid_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_gridState_modifiedAt"))
    op.drop_table("gridState")

    with op.batch_alter_table("gridLastUsedView", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_gridLastUsedView_user_grid_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_gridLastUsedView_modifiedAt"))
    op.drop_table("gridLastUsedView")

    with op.batch_alter_table("sortSpec", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_sortSpec_view_field_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(
            "uq_sortSpec_view_position_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_sortSpec_modifiedAt"))
    op.drop_table("sortSpec")

    with op.batch_alter_table("gridView", schema=None) as batch_op:
        batch_op.drop_index(
            "ix_gridView_gridID_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(
            "uq_gridView_owner_name_live",
            sqlite_where=_LIVE_SAVED_USER_VIEW,
            postgresql_where=_LIVE_SAVED_USER_VIEW,
        )
        batch_op.drop_index(
            "uq_gridView_system_name_live",
            sqlite_where=_LIVE_SYSTEM_VIEW,
            postgresql_where=_LIVE_SYSTEM_VIEW,
        )
        batch_op.drop_index(batch_op.f("ix_gridView_modifiedAt"))
    op.drop_table("gridView")

    with op.batch_alter_table("grid", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_grid_gridKey_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_grid_modifiedAt"))
    op.drop_table("grid")

    with op.batch_alter_table("dataSource", schema=None) as batch_op:
        batch_op.drop_column("exposedFields")
        batch_op.drop_column("visualQueryDefinition")
