"""LookupDataAccess: the lookup permission and grant model (WTK-061, REQ-036)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from mentorapp.access import (
    DataSourceAccessError,
    InMemoryGrantRegistry,
    InMemoryLookupSources,
    LookupBinding,
    LookupUnboundError,
    SourceGrant,
    StoredLookupSources,
    authorize_lookup_search,
    grant_data_source_role,
    is_lookup_searchable,
    revoke_data_source_role,
    stored_lookup_scope,
)
from mentorapp.access.mentoring import (
    MENTOR_LOOKUP_BINDINGS,
    seed_mentor_lookup_bindings,
)
from mentorapp.storage import DataSource, LookupSourceBinding, utcnow
from mentorapp.ui.lookup_control import resolve_suggestions

MENTOR_USER = uuid.uuid4()
ADMIN_USER = uuid.uuid4()

RESOLVER = InMemoryLookupSources([LookupBinding("mentor", "mentorsForLookup")])
GRANTS = InMemoryGrantRegistry(
    [SourceGrant(data_source_key="mentorsForLookup", role_name="mentor")]
)


# --- the one boundary: lookup permission is the bound source's grant ---


def test_granted_role_may_search_the_related_set() -> None:
    assert is_lookup_searchable(
        RESOLVER, GRANTS, related_entity_type="mentor", user_roles=frozenset({"mentor"})
    )


def test_ungranted_role_gets_the_quiet_no() -> None:
    # The control stays rendered; False only selects the noAccess phase.
    assert not is_lookup_searchable(
        RESOLVER, GRANTS, related_entity_type="mentor", user_roles=frozenset({"finance"})
    )


def test_unbound_entity_is_closed_not_open() -> None:
    # Opposite of the areas rule (None means Home, open): an ungoverned
    # lookup reads real records, so unbound must deny by default.
    assert not is_lookup_searchable(
        RESOLVER, GRANTS, related_entity_type="engagement", user_roles=frozenset({"mentor"})
    )


def test_attempt_form_returns_the_key_it_checked() -> None:
    key = authorize_lookup_search(
        RESOLVER,
        GRANTS,
        related_entity_type="mentor",
        user_id=MENTOR_USER,
        user_roles=frozenset({"mentor", "staff"}),
    )
    assert key == "mentorsForLookup"


def test_attempt_without_grant_raises_the_boundary_error() -> None:
    with pytest.raises(DataSourceAccessError):
        authorize_lookup_search(
            RESOLVER,
            GRANTS,
            related_entity_type="mentor",
            user_id=MENTOR_USER,
            user_roles=frozenset({"finance"}),
        )


def test_attempt_on_unbound_entity_is_the_loud_config_error() -> None:
    with pytest.raises(LookupUnboundError):
        authorize_lookup_search(
            RESOLVER,
            GRANTS,
            related_entity_type="engagement",
            user_id=MENTOR_USER,
            user_roles=frozenset({"mentor"}),
        )


def test_rebinding_changes_the_next_decision() -> None:
    resolver = InMemoryLookupSources([LookupBinding("mentor", "mentorsForLookup")])
    resolver.bind(LookupBinding("mentor", "mentorsRestricted"))
    assert not is_lookup_searchable(
        resolver, GRANTS, related_entity_type="mentor", user_roles=frozenset({"mentor"})
    )


# --- the quiet form drives the WTK-060 control's never-hide state ---


def test_quiet_no_renders_as_the_no_access_explainer() -> None:
    has_access = is_lookup_searchable(
        RESOLVER, GRANTS, related_entity_type="mentor", user_roles=frozenset({"finance"})
    )
    outcome = resolve_suggestions(
        "smi",
        related_label="Mentor",
        data_source_key="mentorsForLookup",
        has_access=has_access,
    )
    assert outcome.phase == "noAccess"
    assert outcome.message is not None
    assert "mentorsForLookup" in outcome.message.why


# --- stored composition: authorize + row scope in one call ---


@pytest.fixture()
def scoped_mentor_source(session: Session) -> DataSource:
    source = DataSource(
        data_source_key="mentorsForLookup",
        data_source_name="Mentors for lookup",
        data_source_sql="SELECT * FROM vwMentor",
        user_row_filter="assignedUserID",
    )
    session.add(source)
    session.flush()
    grant_data_source_role(
        session, data_source_key="mentorsForLookup", role_name="mentor", granted_by=ADMIN_USER
    )
    return source


def test_stored_scope_carries_the_row_filter_with_the_grant(
    session: Session, scoped_mentor_source: DataSource
) -> None:
    scope = stored_lookup_scope(
        session,
        RESOLVER,
        related_entity_type="mentor",
        user_id=MENTOR_USER,
        user_roles=frozenset({"mentor"}),
    )
    assert scope.data_source_key == "mentorsForLookup"
    assert scope.related_entity_type == "mentor"
    # User-scoped source: the endpoint must bind the session user on this
    # column — suggestions are the grid's rows, never wider.
    assert scope.user_row_filter == "assignedUserID"


def test_stored_scope_denies_without_a_grant(
    session: Session, scoped_mentor_source: DataSource
) -> None:
    with pytest.raises(DataSourceAccessError):
        stored_lookup_scope(
            session,
            RESOLVER,
            related_entity_type="mentor",
            user_id=MENTOR_USER,
            user_roles=frozenset({"finance"}),
        )


def test_revocation_closes_the_lookup_on_the_next_keystroke(
    session: Session, scoped_mentor_source: DataSource
) -> None:
    revoke_data_source_role(
        session, data_source_key="mentorsForLookup", role_name="mentor", revoked_by=ADMIN_USER
    )
    with pytest.raises(DataSourceAccessError):
        stored_lookup_scope(
            session,
            RESOLVER,
            related_entity_type="mentor",
            user_id=MENTOR_USER,
            user_roles=frozenset({"mentor"}),
        )


def test_binding_to_a_retired_source_denies_like_ungranted(
    session: Session, scoped_mentor_source: DataSource
) -> None:
    # Live-rows-only grants: retiring the source closes every lookup over it
    # with the same refusal a role miss gets — no probing which keys live.
    scoped_mentor_source.deleted_at = utcnow()
    session.flush()
    with pytest.raises(DataSourceAccessError):
        stored_lookup_scope(
            session,
            RESOLVER,
            related_entity_type="mentor",
            user_id=MENTOR_USER,
            user_roles=frozenset({"mentor"}),
        )


# --- the durable resolver + its seed (PI-012) ---------------------------------------


def test_stored_resolver_reads_the_live_binding(session: Session) -> None:
    session.add(
        LookupSourceBinding(related_entity_type="mentor", data_source_key="mentorsForLookup")
    )
    session.flush()
    resolver = StoredLookupSources(session)
    assert resolver.lookup_source_key("mentor") == "mentorsForLookup"
    # An unbound entity is a missing row: None, exactly like the reference.
    assert resolver.lookup_source_key("gadget") is None


def test_stored_resolver_ignores_a_soft_deleted_binding(session: Session) -> None:
    session.add(
        LookupSourceBinding(
            related_entity_type="mentor",
            data_source_key="mentorsForLookup",
            deleted_at=utcnow(),
        )
    )
    session.flush()
    # A retired binding reads unbound — the seam's re-resolve-every-time
    # contract, so a re-bind (new live row) takes over with no restart.
    assert StoredLookupSources(session).lookup_source_key("mentor") is None


def test_seed_bindings_are_idempotent_and_reconcile(session: Session) -> None:
    seed_mentor_lookup_bindings(session)
    resolver = StoredLookupSources(session)
    for related_entity_type, data_source_key in MENTOR_LOOKUP_BINDINGS:
        assert resolver.lookup_source_key(related_entity_type) == data_source_key
    # A second run inserts nothing new — one live binding per entity holds.
    seed_mentor_lookup_bindings(session)
    live = session.scalars(
        LookupSourceBinding.__table__.select().where(
            LookupSourceBinding.deleted_at.is_(None)
        )
    ).all()
    assert len(live) == len(MENTOR_LOOKUP_BINDINGS)
