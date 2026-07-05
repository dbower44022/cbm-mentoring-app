"""Tests for the view/data-source authoring processes (WTK-049, REQ-017..019, 021, 022)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.access.views import (
    CAP_DATA_SOURCE_AUTHOR,
    CAP_VIEW_PROMOTE,
    CapabilityError,
    InMemoryCapabilityRegistry,
    ViewPermissionError,
)
from mentorapp.storage import AppUser, DataSource, Grid, GridView, UserPreference
from mentorapp.storage.adminsql import AdminSqlError
from mentorapp.ui.grid_panel import SOFT_DELETE_HONESTY
from mentorapp.ui.view_authoring import (
    CREATE_VIEW_WALKTHROUGH,
    CalculatedField,
    DataSourceDraft,
    ViewAuthoringError,
    ViewDraft,
    VisualJoin,
    VisualQuery,
    apply_temporary_view,
    author_data_source,
    compile_visual_query,
    create_view,
    delete_user_view,
    delete_view_confirmation,
    promote_view_to_system,
    remember_last_used_view,
    restore_last_used_view,
    save_as_user_view,
)


def _user(session: Session, username: str = "mentor.one") -> AppUser:
    user = AppUser(crm_user_id=f"crm-{username}", username=username)
    session.add(user)
    session.flush()
    return user


def _source(session: Session, key: str = "mentorRoster") -> DataSource:
    source = DataSource(
        data_source_key=key,
        data_source_name="Mentor roster",
        data_source_sql='SELECT "fullName", "status" FROM "vw_mentor"',
        exposed_fields=["fullName", "status"],
    )
    session.add(source)
    session.flush()
    return source


def _grid(session: Session, key: str = "mentorRoster") -> Grid:
    grid = Grid(grid_key=key, grid_name="Mentor roster")
    session.add(grid)
    session.flush()
    return grid


def _draft(source: DataSource, name: str = "My mentors") -> ViewDraft:
    return ViewDraft(
        grid_view_name=name,
        data_source_id=source.data_source_id,
        displayed_fields=({"fieldName": "fullName"}, {"fieldName": "status"}),
        grouping_config={"groupFields": ["status"]},
        row_theme={"rowHeight": "compact"},
    )


def _system_view(session: Session, grid: Grid, source: DataSource, name: str) -> GridView:
    view = GridView(
        grid_id=grid.grid_id,
        data_source_id=source.data_source_id,
        grid_view_name=name,
        view_type="system",
        displayed_fields=[{"fieldName": "fullName"}],
    )
    session.add(view)
    session.flush()
    return view


# --- create_view (REQ-018) ----------------------------------------------------------


def test_walkthrough_steps_match_the_standard() -> None:
    assert CREATE_VIEW_WALKTHROUGH == ("dataSource", "displayedFields", "grouping", "rowTheme")


def test_create_view_commits_the_walkthrough_as_a_saved_user_view(session: Session) -> None:
    user, source, grid = _user(session), _source(session), _grid(session)
    view = create_view(session, grid=grid, draft=_draft(source), user_id=user.user_id)
    assert view.view_type == "user"
    assert view.user_id == user.user_id
    assert not view.temporary_modified_flag
    assert view.grouping_config == {"groupFields": ["status"]}
    assert view.row_theme == {"rowHeight": "compact"}
    assert view.created_by == user.user_id


def test_create_view_rejects_fields_the_source_does_not_expose(session: Session) -> None:
    user, source, grid = _user(session), _source(session), _grid(session)
    draft = ViewDraft(
        grid_view_name="Bad",
        data_source_id=source.data_source_id,
        displayed_fields=({"fieldName": "salary"},),
    )
    with pytest.raises(ViewAuthoringError, match="salary"):
        create_view(session, grid=grid, draft=draft, user_id=user.user_id)


def test_create_view_rejects_a_duplicate_name_among_the_users_views(session: Session) -> None:
    user, source, grid = _user(session), _source(session), _grid(session)
    create_view(session, grid=grid, draft=_draft(source), user_id=user.user_id)
    with pytest.raises(ViewAuthoringError, match="already have a view"):
        create_view(session, grid=grid, draft=_draft(source), user_id=user.user_id)


# --- save_as_user_view / apply_temporary_view (REQ-017) -----------------------------


def test_modified_system_view_saves_as_a_new_user_view(session: Session) -> None:
    user, source, grid = _user(session), _source(session), _grid(session)
    system = _system_view(session, grid, source, "All mentors")
    saved = save_as_user_view(session, view=system, name="Mine", user_id=user.user_id)
    assert saved.grid_view_id != system.grid_view_id
    assert saved.user_id == user.user_id
    assert saved.view_type == "user"
    assert system.deleted_at is None
    assert system.user_id is None


def test_own_saved_view_saves_in_place(session: Session) -> None:
    user, source, grid = _user(session), _source(session), _grid(session)
    view = create_view(session, grid=grid, draft=_draft(source), user_id=user.user_id)
    renamed = save_as_user_view(session, view=view, name="Renamed", user_id=user.user_id)
    assert renamed.grid_view_id == view.grid_view_id
    assert renamed.grid_view_name == "Renamed"


def test_saving_a_temporary_copy_supersedes_it(session: Session) -> None:
    user, source, grid = _user(session), _source(session), _grid(session)
    system = _system_view(session, grid, source, "All mentors")
    copy = apply_temporary_view(
        session,
        base_view=system,
        user_id=user.user_id,
        overrides={"ad_hoc_filter_flag": False},
    )
    saved = save_as_user_view(session, view=copy, name="Tuned", user_id=user.user_id)
    assert saved.grid_view_id != copy.grid_view_id
    assert not saved.temporary_modified_flag
    assert saved.ad_hoc_filter_flag is False
    assert copy.deleted_at is not None


def test_temporary_copy_is_one_per_user_per_grid_and_reused(session: Session) -> None:
    user, source, grid = _user(session), _source(session), _grid(session)
    system = _system_view(session, grid, source, "All mentors")
    first = apply_temporary_view(session, base_view=system, user_id=user.user_id, overrides={})
    second = apply_temporary_view(
        session,
        base_view=system,
        user_id=user.user_id,
        overrides={"row_theme": {"rowHeight": "compact"}},
    )
    assert second.grid_view_id == first.grid_view_id
    assert second.temporary_modified_flag
    assert second.row_theme == {"rowHeight": "compact"}
    assert second.grid_view_name == system.grid_view_name


def test_temporary_application_refuses_another_users_view(session: Session) -> None:
    user, source, grid = _user(session), _source(session), _grid(session)
    other = _user(session, "mentor.two")
    theirs = create_view(session, grid=grid, draft=_draft(source), user_id=other.user_id)
    with pytest.raises(ViewPermissionError):
        apply_temporary_view(session, base_view=theirs, user_id=user.user_id, overrides={})


def test_temporary_overrides_are_validated_like_a_draft(session: Session) -> None:
    user, source, grid = _user(session), _source(session), _grid(session)
    system = _system_view(session, grid, source, "All mentors")
    with pytest.raises(ViewAuthoringError, match="unknown temporary override"):
        apply_temporary_view(
            session, base_view=system, user_id=user.user_id, overrides={"grid_view_name": "x"}
        )
    with pytest.raises(ViewAuthoringError, match="salary"):
        apply_temporary_view(
            session,
            base_view=system,
            user_id=user.user_id,
            overrides={"displayed_fields": [{"fieldName": "salary"}]},
        )


# --- remember & restore the last-used view (REQ-017, REQ-031) -----------------------


def test_last_used_view_round_trips_through_the_preference_row(session: Session) -> None:
    user, source, grid = _user(session), _source(session), _grid(session)
    system = _system_view(session, grid, source, "All mentors")
    mine = create_view(session, grid=grid, draft=_draft(source), user_id=user.user_id)
    remember_last_used_view(session, grid=grid, view=mine, user_id=user.user_id)
    restored = restore_last_used_view(session, grid=grid, user_id=user.user_id)
    assert restored.view is mine
    assert restored.notice is None
    assert system.deleted_at is None
    rows = session.scalars(select(UserPreference)).all()
    assert [row.preference_key for row in rows] == [f"grid.{grid.grid_key}.lastView"]


def test_unset_preference_lands_quietly_on_the_default_system_view(session: Session) -> None:
    user, source, grid = _user(session), _source(session), _grid(session)
    system = _system_view(session, grid, source, "All mentors")
    restored = restore_last_used_view(session, grid=grid, user_id=user.user_id)
    assert restored.view is system
    assert restored.notice is None


def test_stale_remembered_view_falls_back_with_an_educate_notice(session: Session) -> None:
    user, source, grid = _user(session), _source(session), _grid(session)
    system = _system_view(session, grid, source, "All mentors")
    mine = create_view(session, grid=grid, draft=_draft(source), user_id=user.user_id)
    remember_last_used_view(session, grid=grid, view=mine, user_id=user.user_id)
    delete_user_view(session, view=mine, user_id=user.user_id)
    restored = restore_last_used_view(session, grid=grid, user_id=user.user_id)
    assert restored.view is system
    assert restored.notice is not None
    assert "isn't available" in restored.notice.what_happened


# --- promote_view_to_system (REQ-017) -----------------------------------------------


def test_promotion_requires_the_capability(session: Session) -> None:
    user, source, grid = _user(session), _source(session), _grid(session)
    mine = create_view(session, grid=grid, draft=_draft(source), user_id=user.user_id)
    with pytest.raises(CapabilityError):
        promote_view_to_system(
            session, view=mine, lookup=InMemoryCapabilityRegistry(), user_id=user.user_id
        )


def test_promotion_copies_and_never_touches_the_owners_view(session: Session) -> None:
    user, source, grid = _user(session), _source(session), _grid(session)
    admin = _user(session, "admin.one")
    lookup = InMemoryCapabilityRegistry(
        {admin.user_id: frozenset({CAP_VIEW_PROMOTE, CAP_DATA_SOURCE_AUTHOR})}
    )
    mine = create_view(session, grid=grid, draft=_draft(source), user_id=user.user_id)
    promoted = promote_view_to_system(session, view=mine, lookup=lookup, user_id=admin.user_id)
    assert promoted.grid_view_id != mine.grid_view_id
    assert promoted.user_id is None
    assert promoted.view_type == "system"
    assert promoted.grouping_config == mine.grouping_config
    assert mine.user_id == user.user_id
    with pytest.raises(ViewAuthoringError, match="already exists"):
        promote_view_to_system(session, view=mine, lookup=lookup, user_id=admin.user_id)


# --- author_data_source: raw SQL + visual builder (REQ-019) -------------------------


def _author_lookup(user_id: uuid.UUID) -> InMemoryCapabilityRegistry:
    return InMemoryCapabilityRegistry({user_id: frozenset({CAP_DATA_SOURCE_AUTHOR})})


def test_authoring_requires_the_capability(session: Session) -> None:
    user = _user(session)
    draft = DataSourceDraft(
        data_source_key="k",
        data_source_name="K",
        sql_text='SELECT "a" FROM "vw_x"',
        exposed_fields=("a",),
    )
    with pytest.raises(CapabilityError):
        author_data_source(
            session, lookup=InMemoryCapabilityRegistry(), user_id=user.user_id, draft=draft
        )


def test_raw_sql_source_is_validated_then_persisted(session: Session) -> None:
    user = _user(session)
    draft = DataSourceDraft(
        data_source_key="activeMentors",
        data_source_name="Active mentors",
        sql_text='SELECT "fullName" FROM "vw_mentor" WHERE "userID" = :currentUserID',
        exposed_fields=("fullName",),
        user_row_filter="userID",
    )
    source = author_data_source(
        session, lookup=_author_lookup(user.user_id), user_id=user.user_id, draft=draft
    )
    assert source.exposed_fields == ["fullName"]
    assert source.user_row_filter == "userID"
    assert source.visual_query_definition is None
    bad = DataSourceDraft(
        data_source_key="bad",
        data_source_name="Bad",
        sql_text='DELETE FROM "vw_mentor"',
        exposed_fields=("x",),
    )
    with pytest.raises(AdminSqlError):
        author_data_source(
            session, lookup=_author_lookup(user.user_id), user_id=user.user_id, draft=bad
        )


def test_visual_query_compiles_joins_calculated_fields_and_scoping() -> None:
    query = VisualQuery(
        base_view="vw_mentor",
        selected_fields=("vw_mentor.fullName", "vw_session.startsAt"),
        joins=(
            VisualJoin(
                view_name="vw_session",
                left_field="vw_mentor.mentorID",
                right_field="vw_session.mentorID",
            ),
        ),
        calculated_fields=(CalculatedField("sessionCount", 'COUNT("vw_session"."sessionID")'),),
    )
    sql = compile_visual_query(query, user_row_filter="vw_mentor.userID")
    assert sql == (
        'SELECT "vw_mentor"."fullName", "vw_session"."startsAt", '
        '(COUNT("vw_session"."sessionID")) AS "sessionCount" '
        'FROM "vw_mentor" '
        'LEFT JOIN "vw_session" ON "vw_mentor"."mentorID" = "vw_session"."mentorID" '
        'WHERE "vw_mentor"."userID" = :currentUserID'
    )
    assert query.exposed_field_names() == ["fullName", "startsAt", "sessionCount"]


def test_visual_query_rejects_non_identifier_references() -> None:
    with pytest.raises(ViewAuthoringError, match="invalid identifier"):
        compile_visual_query(
            VisualQuery(base_view="vw_x", selected_fields=("a; DROP TABLE b",))
        )


def test_visual_source_persists_document_and_derived_fields(session: Session) -> None:
    user = _user(session)
    query = VisualQuery(base_view="vw_mentor", selected_fields=("vw_mentor.fullName",))
    draft = DataSourceDraft(
        data_source_key="visualMentors",
        data_source_name="Visual mentors",
        visual_query=query,
    )
    source = author_data_source(
        session, lookup=_author_lookup(user.user_id), user_id=user.user_id, draft=draft
    )
    assert source.visual_query_definition == query.as_definition()
    assert source.exposed_fields == ["fullName"]
    # Re-authoring the same live key updates the one canonical row.
    again = author_data_source(
        session,
        lookup=_author_lookup(user.user_id),
        user_id=user.user_id,
        draft=DataSourceDraft(
            data_source_key="visualMentors",
            data_source_name="Visual mentors v2",
            sql_text='SELECT "status" FROM "vw_mentor"',
            exposed_fields=("status",),
        ),
    )
    assert again.data_source_id == source.data_source_id
    assert again.data_source_name == "Visual mentors v2"
    assert again.visual_query_definition is None


def test_authoring_demands_exactly_one_mode(session: Session) -> None:
    user = _user(session)
    with pytest.raises(ViewAuthoringError, match="exactly one"):
        author_data_source(
            session,
            lookup=_author_lookup(user.user_id),
            user_id=user.user_id,
            draft=DataSourceDraft(data_source_key="k", data_source_name="K"),
        )


# --- The destructive step through the one confirmation (REQ-021/REQ-022) ------------


def test_delete_confirmation_carries_the_standard_shape(session: Session) -> None:
    user, source, grid = _user(session), _source(session), _grid(session)
    mine = create_view(session, grid=grid, draft=_draft(source), user_id=user.user_id)
    confirmation = delete_view_confirmation(mine)
    assert confirmation.title == "Delete view 1 record?"
    assert confirmation.listed_titles == ("My mentors",)
    assert confirmation.more_count == 0
    assert confirmation.hidden_rows_notice is None
    assert confirmation.honesty_note == SOFT_DELETE_HONESTY


def test_delete_soft_deletes_own_view_and_refuses_system_views(session: Session) -> None:
    user, source, grid = _user(session), _source(session), _grid(session)
    system = _system_view(session, grid, source, "All mentors")
    mine = create_view(session, grid=grid, draft=_draft(source), user_id=user.user_id)
    delete_user_view(session, view=mine, user_id=user.user_id)
    assert mine.deleted_at is not None
    assert mine.deleted_by == user.user_id
    with pytest.raises(ViewPermissionError):
        delete_user_view(session, view=system, user_id=user.user_id)
