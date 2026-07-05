"""Tests for the grid data model (WTK-041, REQ-016..REQ-031)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mentorapp.storage import (
    SORT_DIRECTIONS,
    VIEW_TYPES,
    AppUser,
    AuthSession,
    DataSource,
    Grid,
    GridDeepLink,
    GridLastUsedView,
    GridSessionState,
    GridState,
    GridView,
    SortSpec,
    utcnow,
)


def _user(session: Session, username: str = "mentor.one") -> AppUser:
    user = AppUser(crm_user_id=f"crm-{username}", username=username)
    session.add(user)
    session.flush()
    return user


def _data_source(session: Session, key: str = "mentorRoster") -> DataSource:
    source = DataSource(
        data_source_key=key,
        data_source_name="Mentor roster",
        data_source_sql='SELECT * FROM "vw_mentor"',
    )
    session.add(source)
    session.flush()
    return source


def _grid(session: Session, key: str = "mentorRoster") -> Grid:
    grid = Grid(grid_key=key, grid_name="Mentor roster")
    session.add(grid)
    session.flush()
    return grid


def _view(
    session: Session,
    grid: Grid,
    source: DataSource,
    name: str = "All mentors",
    *,
    owner: uuid.UUID | None = None,
    view_type: str = "system",
    temporary: bool = False,
) -> GridView:
    view = GridView(
        grid_id=grid.grid_id,
        data_source_id=source.data_source_id,
        grid_view_name=name,
        view_type=view_type,
        user_id=owner,
        temporary_modified_flag=temporary,
    )
    session.add(view)
    session.flush()
    return view


def test_vocabularies_match_the_standard() -> None:
    assert VIEW_TYPES == ("system", "user")
    assert SORT_DIRECTIONS == ("ascending", "descending")


def test_grid_defaults_are_the_standard_locked_behaviors(session: Session) -> None:
    # REQ-016/REQ-024: infinite scrolling, column expansion, and the one
    # keyboard model are the defaults — no grid opts out silently.
    grid = _grid(session)
    session.commit()
    assert grid.infinite_scroll_flag is True
    assert grid.column_expansion_flag is True
    assert grid.keyboard_model_key == "standard"
    assert grid.action_bar_config == {}
    assert grid.status_bar_config == {}


def test_grid_key_is_unique_among_live_rows_only(session: Session) -> None:
    corpse = Grid(grid_key="mentorRoster", grid_name="Mentor roster")
    corpse.deleted_at = utcnow()
    session.add(corpse)
    _grid(session)  # live re-add of a soft-deleted key (REQ-052)
    session.commit()

    session.add(Grid(grid_key="mentorRoster", grid_name="Duplicate"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_system_view_names_collide_but_user_saved_copies_do_not(session: Session) -> None:
    grid = _grid(session)
    source = _data_source(session)
    _view(session, grid, source, "All mentors")
    # A user's saved view may reuse a system view's name (REQ-017: users
    # manage their own views) — different uniqueness partition.
    owner = _user(session)
    _view(session, grid, source, "All mentors", owner=owner.user_id, view_type="user")
    session.commit()

    with pytest.raises(IntegrityError):  # second live system view collides
        _view(session, grid, source, "All mentors")


def test_temporary_modified_copy_never_collides_with_the_saved_view(session: Session) -> None:
    # REQ-017: applying a modification temporarily keeps the base view's name
    # until "save as my view" — the temp copy is outside name uniqueness.
    grid = _grid(session)
    source = _data_source(session)
    owner = _user(session)
    _view(session, grid, source, "My mentors", owner=owner.user_id, view_type="user")
    temp = _view(
        session,
        grid,
        source,
        "My mentors",
        owner=owner.user_id,
        view_type="user",
        temporary=True,
    )
    session.commit()
    assert temp.temporary_modified_flag is True

    # A second SAVED view with the same name still collides for its owner.
    with pytest.raises(IntegrityError):
        _view(session, grid, source, "My mentors", owner=owner.user_id, view_type="user")


def test_sort_specs_are_ordered_and_positions_unique_per_view(session: Session) -> None:
    grid = _grid(session)
    source = _data_source(session)
    view = _view(session, grid, source)
    session.add_all(
        [
            SortSpec(
                grid_view_id=view.grid_view_id,
                sort_field_name="mentorName",
                sort_direction="ascending",
                sort_position=2,
            ),
            SortSpec(
                grid_view_id=view.grid_view_id,
                sort_field_name="engagementStatus",
                sort_direction="descending",
                sort_position=1,
            ),
        ]
    )
    session.commit()
    session.refresh(view)

    # REQ-025: the numbered badge order is the relationship order.
    assert [s.sort_field_name for s in view.sort_specs] == ["engagementStatus", "mentorName"]

    session.add(
        SortSpec(
            grid_view_id=view.grid_view_id,
            sort_field_name="mentorCapacity",
            sort_direction="ascending",
            sort_position=1,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_last_used_view_is_one_row_per_user_and_grid(session: Session) -> None:
    grid = _grid(session)
    source = _data_source(session)
    view = _view(session, grid, source)
    user = _user(session)
    session.add(
        GridLastUsedView(
            user_id=user.user_id, grid_id=grid.grid_id, grid_view_id=view.grid_view_id
        )
    )
    session.commit()

    session.add(
        GridLastUsedView(
            user_id=user.user_id, grid_id=grid.grid_id, grid_view_id=view.grid_view_id
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_grid_state_keeps_recent_searches_per_user_and_grid(session: Session) -> None:
    grid = _grid(session)
    user = _user(session)
    state = GridState(
        user_id=user.user_id,
        grid_id=grid.grid_id,
        recent_searches=["smith", "jones"],
    )
    session.add(state)
    session.commit()

    found = session.scalars(select(GridState).where(GridState.user_id == user.user_id)).one()
    assert found.recent_searches == ["smith", "jones"]


def test_session_state_round_trips_the_restore_payload(session: Session) -> None:
    # REQ-031: view, search, scroll, selection, and focus restore exactly.
    grid = _grid(session)
    source = _data_source(session)
    view = _view(session, grid, source)
    user = _user(session)
    auth_session = AuthSession(
        user_id=user.user_id,
        session_secret_hash="0" * 64,
        session_expires_at=utcnow() + timedelta(hours=8),
    )
    session.add(auth_session)
    session.flush()

    session.add(
        GridSessionState(
            auth_session_id=auth_session.auth_session_id,
            grid_id=grid.grid_id,
            grid_view_id=view.grid_view_id,
            search_text="smi",
            scroll_position=240,
            selected_record_ids=["rec-1", "rec-2"],
            focused_record_id="rec-2",
        )
    )
    session.commit()

    restored = session.scalars(
        select(GridSessionState).where(
            GridSessionState.auth_session_id == auth_session.auth_session_id,
            GridSessionState.grid_id == grid.grid_id,
        )
    ).one()
    assert restored.grid_view_id == view.grid_view_id
    assert restored.search_text == "smi"
    assert restored.scroll_position == 240
    assert restored.selected_record_ids == ["rec-1", "rec-2"]
    assert restored.focused_record_id == "rec-2"

    session.add(
        GridSessionState(auth_session_id=auth_session.auth_session_id, grid_id=grid.grid_id)
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_deep_link_names_a_grid_and_view_by_unique_key(session: Session) -> None:
    grid = _grid(session)
    source = _data_source(session)
    view = _view(session, grid, source)
    session.add(
        GridDeepLink(
            deep_link_key="mentor-roster-all",
            grid_id=grid.grid_id,
            grid_view_id=view.grid_view_id,
        )
    )
    session.commit()

    # REQ-028: the key is the URL identity — one live row per key.
    session.add(GridDeepLink(deep_link_key="mentor-roster-all", grid_id=grid.grid_id))
    with pytest.raises(IntegrityError):
        session.commit()


def test_data_source_authoring_columns_default_to_raw_sql(session: Session) -> None:
    # REQ-019: null visualQueryDefinition = raw-SQL-authored; exposedFields
    # is the bound on what views may display.
    source = _data_source(session)
    session.commit()
    assert source.visual_query_definition is None
    assert source.exposed_fields == []

    source.visual_query_definition = {
        "joins": [{"table": "engagement", "on": "mentorID"}],
        "filters": [{"field": "engagementStatus", "operator": "eq", "value": "active"}],
        "calculatedFields": [{"name": "activeEngagementCount", "expression": "count(*)"}],
    }
    source.exposed_fields = ["mentorName", "engagementStatus", "activeEngagementCount"]
    session.commit()
    session.refresh(source)
    assert source.exposed_fields == [
        "mentorName",
        "engagementStatus",
        "activeEngagementCount",
    ]
