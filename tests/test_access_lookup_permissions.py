"""Permission-scoped type-ahead verification: the composed read (WTK-082, REQ-036).

WTK-060/061/072 each test their own layer; this suite verifies the edges that
only exist once the layers compose in ``suggest_related_records`` — the order
access and liveness decide in, the grant lifecycle as the user keeps typing,
and that a scoped or denied search can never widen into rows the governing
source would not show.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from mentorapp.access import (
    InMemoryLookupSources,
    LookupBinding,
    grant_data_source_role,
    revoke_data_source_role,
)
from mentorapp.api.lookup_suggestions import suggest_related_records
from mentorapp.storage import DataSource, SchemaRegistry
from mentorapp.storage.mentoring import ProgressGoal
from mentorapp.ui.lookup_control import SuggestionOutcome

MENTOR_USER = uuid.uuid4()
OTHER_USER = uuid.uuid4()
ADMIN_USER = uuid.uuid4()

SOURCE_KEY = "progressGoalsForLookup"
RESTRICTED_KEY = "progressGoalsRestricted"
RESOLVER = InMemoryLookupSources([LookupBinding("progressGoal", SOURCE_KEY)])


def _suggest(
    session: Session,
    search_text: str,
    *,
    resolver: InMemoryLookupSources = RESOLVER,
    user_roles: frozenset[str] = frozenset({"mentor"}),
) -> SuggestionOutcome:
    return suggest_related_records(
        session,
        resolver,
        entity_cls=ProgressGoal,
        field_name="progressGoalID",
        search_text=search_text,
        user_id=MENTOR_USER,
        user_roles=user_roles,
        related_label="Progress goal",
    )


def _goal(description: str, *, created_by: uuid.UUID = MENTOR_USER) -> ProgressGoal:
    return ProgressGoal(progress_goal_description=description, created_by=created_by)


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


# --- access decides before liveness: the affordance itself is permissioned ---------


def test_denial_wins_over_keep_typing(session: Session, lookup_source: DataSource) -> None:
    # Short text from an ungranted role must not earn the keepTyping invite —
    # that would advertise a search the user is not allowed to run.
    for text in ("", "bu", "budget"):
        outcome = _suggest(session, text, user_roles=frozenset({"finance"}))
        assert outcome.phase == "noAccess"
        assert outcome.message is not None and SOURCE_KEY in outcome.message.why


def test_unbound_wins_over_keep_typing(session: Session, lookup_source: DataSource) -> None:
    # Same ordering for the configuration defect: an ungoverned lookup never
    # invites typing, whatever the text length.
    for text in ("", "bu", "budget"):
        outcome = _suggest(session, text, resolver=InMemoryLookupSources())
        assert outcome.phase == "noAccess"
        assert outcome.message is not None and "data source" in outcome.message.why


def test_denial_returns_no_rows_and_no_count(
    session: Session, lookup_source: DataSource
) -> None:
    # Matching rows exist, but a denied outcome must carry zero evidence of
    # them — neither a window nor the full-set count may leak.
    session.add(_goal("Budget review"))
    session.flush()
    outcome = _suggest(session, "budget", user_roles=frozenset({"finance"}))
    assert (outcome.suggestions, outcome.total_matches) == ((), 0)


# --- the grant lifecycle plays out keystroke by keystroke ---------------------------


def test_any_granted_role_opens_the_lookup(session: Session, lookup_source: DataSource) -> None:
    session.add(_goal("Budget review"))
    session.flush()
    outcome = _suggest(session, "budget", user_roles=frozenset({"finance", "mentor"}))
    assert outcome.phase == "matches"
    assert [ref.title for ref in outcome.suggestions] == ["Budget review"]


def test_revocation_closes_the_next_keystroke(
    session: Session, lookup_source: DataSource
) -> None:
    session.add(_goal("Budget review"))
    session.flush()
    assert _suggest(session, "budget").phase == "matches"
    revoke_data_source_role(
        session, data_source_key=SOURCE_KEY, role_name="mentor", revoked_by=ADMIN_USER
    )
    outcome = _suggest(session, "budget")
    assert outcome.phase == "noAccess"
    assert outcome.message is not None and SOURCE_KEY in outcome.message.why


def test_a_new_grant_opens_the_next_keystroke(
    session: Session, lookup_source: DataSource
) -> None:
    session.add(_goal("Budget review"))
    session.flush()
    assert _suggest(session, "budget", user_roles=frozenset({"staff"})).phase == "noAccess"
    grant_data_source_role(
        session, data_source_key=SOURCE_KEY, role_name="staff", granted_by=ADMIN_USER
    )
    assert _suggest(session, "budget", user_roles=frozenset({"staff"})).phase == "matches"


def test_rebinding_swaps_the_governing_permission(
    session: Session, lookup_source: DataSource
) -> None:
    # The binding names the boundary: pointing the entity at a source the
    # user's roles don't cover must deny, and the explainer must name the
    # NEW governing key — the one the decision actually used.
    resolver = InMemoryLookupSources([LookupBinding("progressGoal", SOURCE_KEY)])
    session.add(_goal("Budget review"))
    session.flush()
    assert _suggest(session, "budget", resolver=resolver).phase == "matches"
    resolver.bind(LookupBinding("progressGoal", RESTRICTED_KEY))
    outcome = _suggest(session, "budget", resolver=resolver)
    assert outcome.phase == "noAccess"
    assert outcome.message is not None and RESTRICTED_KEY in outcome.message.why


# --- a user-scoped source scopes the whole suggestion answer ------------------------


def test_empty_owned_set_is_no_matches_not_no_access(
    session: Session, lookup_source: DataSource
) -> None:
    # An empty permitted set is a search result, not a denial: the user keeps
    # the create-new affordance instead of being told to chase a grant.
    lookup_source.user_row_filter = "createdBy"
    session.add(_goal("Budget theirs", created_by=OTHER_USER))
    session.flush()
    outcome = _suggest(session, "budget")
    assert outcome.phase == "noMatches"
    assert outcome.message is not None and "New…" in outcome.message.what_next


def test_scoped_window_and_count_never_widen(
    session: Session, lookup_source: DataSource
) -> None:
    # More owned matches than the window PLUS foreign rows: the count is the
    # owned total (never the unscoped one) and no foreign row reaches the
    # window even when the owned set can't fill it alone.
    lookup_source.user_row_filter = "createdBy"
    session.add_all([_goal(f"Budget mine {n}") for n in range(10)])
    session.add_all([_goal(f"Budget theirs {n}", created_by=OTHER_USER) for n in range(5)])
    session.flush()
    outcome = _suggest(session, "budget")
    assert outcome.total_matches == 10
    assert len(outcome.suggestions) == 8
    assert all(ref.title.startswith("Budget mine") for ref in outcome.suggestions)
