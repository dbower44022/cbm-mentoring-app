"""The relationship type-ahead suggestion read (WTK-072, REQ-036)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from mentorapp.access import InMemoryLookupSources, LookupBinding, grant_data_source_role
from mentorapp.api.lookup_suggestions import suggest_related_records, suggestion_title
from mentorapp.api.records import registry_for
from mentorapp.storage import DataSource, SchemaRegistry, utcnow
from mentorapp.storage.mentoring import ProgressGoal
from mentorapp.ui.lookup_control import SuggestionOutcome

MENTOR_USER = uuid.uuid4()
OTHER_USER = uuid.uuid4()
ADMIN_USER = uuid.uuid4()

SOURCE_KEY = "progressGoalsForLookup"
RESOLVER = InMemoryLookupSources([LookupBinding("progressGoal", SOURCE_KEY)])
MENTOR_ROLES = frozenset({"mentor"})


def _suggest(
    session: Session,
    search_text: str,
    *,
    resolver: InMemoryLookupSources = RESOLVER,
    field_name: str = "progressGoalID",
    user_roles: frozenset[str] = MENTOR_ROLES,
) -> SuggestionOutcome:
    return suggest_related_records(
        session,
        resolver,
        entity_cls=ProgressGoal,
        field_name=field_name,
        search_text=search_text,
        user_id=MENTOR_USER,
        user_roles=user_roles,
        related_label="Progress goal",
    )


@pytest.fixture()
def lookup_source(session: Session) -> DataSource:
    session.add(
        SchemaRegistry(
            entity_type="progressGoal",
            field_name="progressGoalDescription",
            field_type="text",
            field_label="Description",
            searchable_flag=True,
        )
    )
    source = DataSource(
        data_source_key=SOURCE_KEY,
        data_source_name="Progress goals for lookup",
        data_source_sql="SELECT * FROM vwProgressGoal",
    )
    session.add(source)
    session.flush()
    grant_data_source_role(
        session, data_source_key=SOURCE_KEY, role_name="mentor", granted_by=ADMIN_USER
    )
    return source


def _goal(description: str, *, created_by: uuid.UUID = MENTOR_USER) -> ProgressGoal:
    return ProgressGoal(progress_goal_description=description, created_by=created_by)


def test_matching_rows_come_back_with_the_full_set_count(
    session: Session, lookup_source: DataSource
) -> None:
    session.add_all(
        [
            _goal("Improve budgeting skills"),
            _goal("Budget review cadence"),
            _goal("Find housing"),
        ]
    )
    session.flush()
    outcome = _suggest(session, "budget")
    assert outcome.phase == "matches"
    assert outcome.total_matches == 2
    assert {ref.title for ref in outcome.suggestions} == {
        "Improve budgeting skills",
        "Budget review cadence",
    }
    assert all(ref.entity_type == "progressGoal" for ref in outcome.suggestions)


def test_soft_deleted_rows_never_suggest(session: Session, lookup_source: DataSource) -> None:
    removed = _goal("Budget archived")
    removed.deleted_at = utcnow()
    session.add_all([removed, _goal("Budget live")])
    session.flush()
    outcome = _suggest(session, "budget")
    assert outcome.total_matches == 1
    assert [ref.title for ref in outcome.suggestions] == ["Budget live"]


def test_the_window_renders_but_the_count_is_the_truth(
    session: Session, lookup_source: DataSource
) -> None:
    session.add_all([_goal(f"Budget goal {n}") for n in range(10)])
    session.flush()
    outcome = _suggest(session, "budget")
    assert len(outcome.suggestions) == 8
    assert outcome.total_matches == 10
    assert outcome.summary is not None and "showing the first 8" in outcome.summary


def test_short_text_educates_and_runs_no_query(
    session: Session, lookup_source: DataSource
) -> None:
    assert _suggest(session, "bu").phase == "keepTyping"
    assert _suggest(session, "   ").phase == "idle"


def test_a_role_without_the_grant_gets_the_no_access_explainer(
    session: Session, lookup_source: DataSource
) -> None:
    outcome = _suggest(session, "budget", user_roles=frozenset({"finance"}))
    assert outcome.phase == "noAccess"
    assert outcome.message is not None and SOURCE_KEY in outcome.message.why


def test_an_unbound_entity_explains_the_configuration_gap(
    session: Session, lookup_source: DataSource
) -> None:
    outcome = _suggest(session, "budget", resolver=InMemoryLookupSources())
    assert outcome.phase == "noAccess"
    # A config defect, not a role miss: the fix named is binding a source.
    assert outcome.message is not None and "data source" in outcome.message.why
    assert "roles" not in outcome.message.why


def test_a_user_scoped_source_scopes_count_and_window(
    session: Session, lookup_source: DataSource
) -> None:
    lookup_source.user_row_filter = "createdBy"
    session.add_all([_goal("Budget mine"), _goal("Budget theirs", created_by=OTHER_USER)])
    session.flush()
    outcome = _suggest(session, "budget")
    assert outcome.total_matches == 1
    assert [ref.title for ref in outcome.suggestions] == ["Budget mine"]


def test_a_missing_row_filter_column_fails_loudly_never_unscoped(
    session: Session, lookup_source: DataSource
) -> None:
    lookup_source.user_row_filter = "assignedUserID"
    session.add(_goal("Budget mine"))
    session.flush()
    with pytest.raises(RuntimeError, match="assignedUserID"):
        _suggest(session, "budget")


def test_a_title_never_renders_blank(session: Session, lookup_source: DataSource) -> None:
    goal = _goal("")
    session.add(goal)
    session.flush()
    registry = registry_for(session, "progressGoal")
    assert suggestion_title(goal, registry) == str(goal.progress_goal_id)


def test_a_non_reference_field_name_is_a_caller_bug(
    session: Session, lookup_source: DataSource
) -> None:
    with pytest.raises(ValueError, match="reference"):
        _suggest(session, "budget", field_name="progressGoalDescription")
