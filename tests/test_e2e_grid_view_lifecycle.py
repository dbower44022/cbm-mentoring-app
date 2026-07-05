"""End-to-end grid view lifecycle and state-restoration scenarios (WTK-054).

Chained user journeys — not per-verb units (``test_ui_view_authoring`` owns
those): each scenario drives the WTK-049 lifecycle verbs and the live
``/grids/{key}/rows`` surface together over real storage, so every view the
lifecycle produces is proven by the rows it actually serves. REQ-018: the
create-view walkthrough commits into a queryable user view. REQ-017:
apply-temporarily → save-as → promote, each step read back through the API.
REQ-031: returning from a record restores the session bundle exactly while
the data refreshes underneath — edited rows show new values, and a selected
row that dropped out of the view's filter keeps its selection with notice.

The record-edit surface is read-only today (``/records/.../preview``), so the
away-time edit mutates storage directly — the subject here is the grid's
return behavior, not the editor.

PostalCode is the guinea-pig entity as in ``test_api_grids_router`` — the
surface is generic; nothing here is postal-specific.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.access.views import CAP_VIEW_PROMOTE, InMemoryCapabilityRegistry
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.routers.grids import get_grid_entity_catalog
from mentorapp.main import create_app
from mentorapp.storage import (
    AppUser,
    DataSource,
    Grid,
    GridView,
    PostalCode,
    SchemaRegistry,
    SortSpec,
)
from mentorapp.ui.grid_panel import STATE_RESTORATION, ViewSelection, row_count_label
from mentorapp.ui.view_authoring import (
    CREATE_VIEW_WALKTHROUGH,
    ViewDraft,
    apply_temporary_view,
    create_view,
    promote_view_to_system,
    remember_last_used_view,
    restore_last_used_view,
    save_as_user_view,
)

ENTITY = "postalCode"
GRID_KEY = "postalDirectory"


class _Catalog:
    """Test catalog: the one entity-backed source resolves to PostalCode."""

    def entity_for(self, data_source_key: str) -> tuple[str, type[Any]] | None:
        return (ENTITY, PostalCode) if data_source_key == "postalCodes" else None


@pytest.fixture()
def mentor(session: Session) -> AppUser:
    user = AppUser(crm_user_id="crm-mentor.one", username="mentor.one")
    session.add(user)
    session.flush()
    return user


@pytest.fixture()
def admin(session: Session) -> AppUser:
    user = AppUser(crm_user_id="crm-admin.one", username="admin.one")
    session.add(user)
    session.flush()
    return user


@pytest.fixture()
def world(session: Session) -> Grid:
    """One grid + 'Oregon' system view over four seeded postal codes."""
    for field_name, searchable in [
        ("postalCodeValue", False),
        ("cityName", True),
        ("stateCode", False),
    ]:
        session.add(
            SchemaRegistry(
                entity_type=ENTITY,
                field_name=field_name,
                field_type="text",
                field_label=field_name,
                searchable_flag=searchable,
            )
        )
    source = DataSource(
        data_source_key="postalCodes",
        data_source_name="Postal codes",
        data_source_sql='SELECT * FROM "vw_postalCode"',
        exposed_fields=["postalCodeValue", "cityName", "stateCode"],
    )
    grid = Grid(grid_key=GRID_KEY, grid_name="Postal directory")
    session.add_all([source, grid])
    session.flush()
    view = GridView(
        grid_id=grid.grid_id,
        data_source_id=source.data_source_id,
        grid_view_name="Oregon",
        view_type="system",
        displayed_fields=[
            {"fieldName": "cityName", "columnWidth": 120, "columnFormat": None},
            {"fieldName": "postalCodeValue", "columnWidth": 80, "columnFormat": None},
        ],
        view_filters={"stateCode": "OR"},
    )
    session.add(view)
    session.flush()
    session.add(
        SortSpec(
            grid_view_id=view.grid_view_id,
            sort_field_name="cityName",
            sort_direction="ascending",
            sort_position=1,
        )
    )
    session.add_all(
        [
            PostalCode(postal_code_value="97035", city_name="Lake Oswego", state_code="OR"),
            PostalCode(postal_code_value="97401", city_name="Eugene", state_code="OR"),
            PostalCode(postal_code_value="97201", city_name="Portland", state_code="OR"),
            PostalCode(postal_code_value="99201", city_name="Spokane", state_code="WA"),
        ]
    )
    session.commit()
    return grid


@pytest.fixture()
def client(session: Session, mentor: AppUser) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user_id] = lambda: mentor.user_id
    app.dependency_overrides[get_grid_entity_catalog] = _Catalog
    return TestClient(app)


def _rows(client: TestClient, view_id: uuid.UUID, **params: Any) -> list[dict[str, Any]]:
    response = client.get(f"/grids/{GRID_KEY}/rows", params={"view_id": str(view_id), **params})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _cities(client: TestClient, view_id: uuid.UUID) -> list[str]:
    return [row["cityName"] for row in _rows(client, view_id)]


# --- Scenario: the create-view walkthrough, committed and queried (REQ-018) --------


def test_create_view_walkthrough_commits_a_view_the_grid_serves(
    session: Session, client: TestClient, mentor: AppUser, world: Grid
) -> None:
    source = session.scalars(select(DataSource)).one()
    # The walkthrough assembles the draft strictly in the standard's step
    # order — the scenario builds it the way the process presents it.
    steps: dict[str, Any] = {}
    for step in CREATE_VIEW_WALKTHROUGH:
        steps[step] = {
            "dataSource": source.data_source_id,
            "displayedFields": ({"fieldName": "cityName"}, {"fieldName": "stateCode"}),
            "grouping": {"groupFields": ["stateCode"]},
            "rowTheme": {"rowHeight": "compact"},
        }[step]
    assert list(steps) == ["dataSource", "displayedFields", "grouping", "rowTheme"]
    view = create_view(
        session,
        grid=world,
        draft=ViewDraft(
            grid_view_name="Washington only",
            data_source_id=steps["dataSource"],
            displayed_fields=steps["displayedFields"],
            grouping_config=steps["grouping"],
            row_theme=steps["rowTheme"],
            view_filters={"stateCode": "WA"},
        ),
        user_id=mentor.user_id,
    )
    session.commit()
    # End to end: the committed view is immediately a live, queryable surface.
    assert _cities(client, view.grid_view_id) == ["Spokane"]
    remember_last_used_view(session, grid=world, view=view, user_id=mentor.user_id)
    restored = restore_last_used_view(session, grid=world, user_id=mentor.user_id)
    assert restored.view is not None
    assert restored.view.grid_view_id == view.grid_view_id
    assert restored.notice is None


# --- Scenario: apply-temporarily → save-as → promote, read back live (REQ-017) -----


def test_temporary_save_as_promote_chain_serves_rows_at_every_step(
    session: Session, client: TestClient, mentor: AppUser, admin: AppUser, world: Grid
) -> None:
    # A fresh user opens the grid: no preference yet, so the default system
    # view answers quietly and serves its filtered rows.
    opened = restore_last_used_view(session, grid=world, user_id=mentor.user_id)
    assert opened.view is not None
    assert opened.view.grid_view_name == "Oregon"
    assert opened.notice is None
    assert _cities(client, opened.view.grid_view_id) == [
        "Eugene",
        "Lake Oswego",
        "Portland",
    ]

    # The user modifies the (read-only) system view and applies temporarily:
    # the selector marks the temporary state, and the working copy is a live
    # view the rows surface serves — nothing shared was touched.
    selector = ViewSelection(opened.view.grid_view_name)
    assert selector.modify("viewSettings").is_modified
    temp = apply_temporary_view(
        session,
        base_view=opened.view,
        user_id=mentor.user_id,
        overrides={"view_filters": {"stateCode": "WA"}},
    )
    session.commit()
    assert temp.temporary_modified_flag
    assert _cities(client, temp.grid_view_id) == ["Spokane"]
    assert _cities(client, opened.view.grid_view_id) == [
        "Eugene",
        "Lake Oswego",
        "Portland",
    ]

    # Save-as makes the settings the user's own view; the temporary copy is
    # superseded (soft-deleted), which is what clears the modified flag.
    saved = save_as_user_view(session, view=temp, name="Washington", user_id=mentor.user_id)
    session.commit()
    assert saved.view_type == "user"
    assert saved.user_id == mentor.user_id
    assert temp.deleted_at is not None
    assert not selector.save_as_user_view(saved.grid_view_name).is_modified
    assert _cities(client, saved.grid_view_id) == ["Spokane"]
    remember_last_used_view(session, grid=world, view=saved, user_id=mentor.user_id)
    assert restore_last_used_view(session, grid=world, user_id=mentor.user_id).view is saved

    # An admin promotes it: a COPY becomes the shared system view; the
    # owner's view is untouched and both serve identical rows.
    lookup = InMemoryCapabilityRegistry({admin.user_id: frozenset({CAP_VIEW_PROMOTE})})
    promoted = promote_view_to_system(session, view=saved, lookup=lookup, user_id=admin.user_id)
    session.commit()
    assert promoted.view_type == "system"
    assert promoted.user_id is None
    assert promoted.grid_view_id != saved.grid_view_id
    assert saved.user_id == mentor.user_id
    assert _cities(client, promoted.grid_view_id) == ["Spokane"]


# --- Scenario: returning from a record restores exactly, data fresh (REQ-031) ------


def test_returning_from_a_record_restores_state_over_refreshed_data(
    session: Session, client: TestClient, mentor: AppUser, world: Grid
) -> None:
    opened = restore_last_used_view(session, grid=world, user_id=mentor.user_id)
    assert opened.view is not None
    before = _rows(client, opened.view.grid_view_id)
    assert [row["cityName"] for row in before] == ["Eugene", "Lake Oswego", "Portland"]
    by_city = {row["cityName"]: row["postalCodeID"] for row in before}

    # The session bundle the panel holds when the user leaves for a record —
    # one piece per STATE_RESTORATION rule, and only activeView is long-term.
    selector = ViewSelection(opened.view.grid_view_name)
    selector.modify("sort")
    bundle = {
        "activeView": opened.view.grid_view_id,
        "temporaryViewModifications": selector.state(),
        "searchText": "lake",
        "scrollPosition": 120,
        "selection": (by_city["Eugene"], by_city["Portland"]),
        "focusedRow": 2,
    }
    assert set(bundle) == {rule.piece for rule in STATE_RESTORATION}
    scopes = {rule.piece: rule.scope for rule in STATE_RESTORATION}
    assert scopes["activeView"] == "longTerm"
    assert all(
        scope == "sessionOnly" for piece, scope in scopes.items() if piece != "activeView"
    )
    remember_last_used_view(session, grid=world, view=opened.view, user_id=mentor.user_id)

    # While the user is away on the record, the world moves: Portland is
    # renamed, and selected Eugene stops matching the view's OR filter.
    portland = session.get(PostalCode, uuid.UUID(by_city["Portland"]))
    eugene = session.get(PostalCode, uuid.UUID(by_city["Eugene"]))
    assert portland is not None and eugene is not None
    portland.city_name = "Portland Pearl"
    eugene.state_code = "WA"
    session.commit()

    # Return: the long-term piece restores through the preference row, the
    # session bundle is intact, and the SAME view now serves fresh data —
    # the edited row shows its new value; Eugene is out of the filter.
    restored = restore_last_used_view(session, grid=world, user_id=mentor.user_id)
    assert restored.view is not None
    assert restored.view.grid_view_id == bundle["activeView"]
    assert restored.notice is None
    assert bundle["temporaryViewModifications"].is_modified
    after = _rows(client, restored.view.grid_view_id)
    assert [row["cityName"] for row in after] == ["Lake Oswego", "Portland Pearl"]

    # Keep-with-notice: the dropped-out row stays selected, and the status
    # bar says so instead of silently shrinking the selection.
    served_ids = {row["postalCodeID"] for row in after}
    selection: tuple[str, ...] = bundle["selection"]
    hidden = [record_id for record_id in selection if record_id not in served_ids]
    assert hidden == [by_city["Eugene"]]
    label = row_count_label(
        len(after), selected_count=len(selection), hidden_selected_count=len(hidden)
    )
    assert label == "2 rows, 2 Selected (1 not in current filter)"
