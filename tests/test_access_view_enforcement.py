"""WTK-050: stored view/data-source permission enforcement and deep-link facts.

REQ-017 — the stored entry points load-and-authorize in one call: system
views refuse management for everyone, users manage exactly their own saved
views, promotion needs the persisted ``gridView.promote`` grant. REQ-019 —
the stored authoring gate admits only ``adminSql.author`` holders. REQ-028 —
``load_deep_link_facts`` assembles exactly what the pure resolver consumes,
with the data-source fact decided through the one stored grant boundary; the
composition tests drive the full resolution outcomes over persisted rows.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from mentorapp.access.grants import grant_data_source_role
from mentorapp.access.view_enforcement import (
    DeepLinkNotFoundError,
    GridOnlyLinkFacts,
    NamedViewLinkFacts,
    ViewNotFoundError,
    authorize_stored_data_source_authoring,
    authorize_stored_view_management,
    authorize_stored_view_promotion,
    load_deep_link_facts,
    stored_save_disposition,
    stored_view_visible_to,
)
from mentorapp.access.views import (
    CAP_DATA_SOURCE_AUTHOR,
    CAP_VIEW_PROMOTE,
    SAVE_AS_USER,
    SAVE_IN_PLACE,
    CapabilityError,
    ViewPermissionError,
)
from mentorapp.api.grid_surface import (
    FallbackToLastUsed,
    GridLink,
    LinkAccessDenied,
    OpenLinkedView,
    resolve_grid_link,
)
from mentorapp.storage import (
    AccessGrant,
    AppUser,
    DataSource,
    Grid,
    GridDeepLink,
    GridView,
    utcnow,
)

MENTOR_ROLES = frozenset({"mentor"})


def seed_user(session: Session, username: str) -> AppUser:
    user = AppUser(crm_user_id=f"crm-{username}", username=username)
    session.add(user)
    session.flush()
    return user


def seed_grid_source(session: Session) -> tuple[Grid, DataSource]:
    grid = Grid(grid_key="engagements", grid_name="Engagements")
    source = DataSource(
        data_source_key="engagementsForMentor",
        data_source_name="Engagements for mentor",
        data_source_sql="SELECT 1",
    )
    session.add_all([grid, source])
    session.flush()
    return grid, source


def seed_view(
    session: Session,
    grid: Grid,
    source: DataSource,
    *,
    owner: AppUser | None = None,
    name: str = "All engagements",
    **overrides: object,
) -> GridView:
    view = GridView(
        grid_id=grid.grid_id,
        data_source_id=source.data_source_id,
        grid_view_name=name,
        view_type="system" if owner is None else "user",
        user_id=None if owner is None else owner.user_id,
        **overrides,
    )
    session.add(view)
    session.flush()
    return view


def seed_link(session: Session, grid: Grid, view: GridView | None) -> GridDeepLink:
    link = GridDeepLink(
        deep_link_key="lnk-1",
        grid_id=grid.grid_id,
        grid_view_id=None if view is None else view.grid_view_id,
    )
    session.add(link)
    session.flush()
    return link


def grant_capability(session: Session, user: AppUser, capability: str) -> None:
    session.add(AccessGrant(user_id=user.user_id, access_grant_key=capability))
    session.flush()


# --- Stored view lifecycle enforcement (REQ-017) -----------------------------------


def test_owner_manages_their_own_saved_view_and_gets_the_authorized_row(
    session: Session,
) -> None:
    mentor = seed_user(session, "mentor.sam")
    grid, source = seed_grid_source(session)
    view = seed_view(session, grid, source, owner=mentor)
    row = authorize_stored_view_management(
        session, grid_view_id=view.grid_view_id, user_id=mentor.user_id, action="rename"
    )
    assert row is view
    assert (
        stored_save_disposition(session, grid_view_id=view.grid_view_id, user_id=mentor.user_id)
        == SAVE_IN_PLACE
    )


def test_system_view_is_read_only_for_everyone(session: Session) -> None:
    mentor = seed_user(session, "mentor.sam")
    grid, source = seed_grid_source(session)
    view = seed_view(session, grid, source)
    with pytest.raises(ViewPermissionError):
        authorize_stored_view_management(
            session, grid_view_id=view.grid_view_id, user_id=mentor.user_id, action="update"
        )
    assert (
        stored_save_disposition(session, grid_view_id=view.grid_view_id, user_id=mentor.user_id)
        == SAVE_AS_USER
    )


def test_foreign_user_view_refuses_management(session: Session) -> None:
    owner = seed_user(session, "mentor.sam")
    other = seed_user(session, "mentor.alex")
    grid, source = seed_grid_source(session)
    view = seed_view(session, grid, source, owner=owner)
    with pytest.raises(ViewPermissionError):
        authorize_stored_view_management(
            session, grid_view_id=view.grid_view_id, user_id=other.user_id, action="delete"
        )
    assert stored_view_visible_to(
        session, grid_view_id=view.grid_view_id, user_id=owner.user_id
    )
    assert not stored_view_visible_to(
        session, grid_view_id=view.grid_view_id, user_id=other.user_id
    )


def test_dead_and_unknown_views_answer_identically(session: Session) -> None:
    mentor = seed_user(session, "mentor.sam")
    grid, source = seed_grid_source(session)
    view = seed_view(session, grid, source, owner=mentor)
    view.deleted_at = utcnow()
    session.flush()
    for view_id in (view.grid_view_id, uuid.uuid4()):
        with pytest.raises(ViewNotFoundError):
            authorize_stored_view_management(
                session, grid_view_id=view_id, user_id=mentor.user_id, action="update"
            )


def test_promotion_needs_the_stored_capability_and_a_saved_user_view(
    session: Session,
) -> None:
    admin = seed_user(session, "admin.pat")
    mentor = seed_user(session, "mentor.sam")
    grid, source = seed_grid_source(session)
    view = seed_view(session, grid, source, owner=mentor)
    with pytest.raises(CapabilityError):
        authorize_stored_view_promotion(
            session, grid_view_id=view.grid_view_id, user_id=admin.user_id
        )
    grant_capability(session, admin, CAP_VIEW_PROMOTE)
    row = authorize_stored_view_promotion(
        session, grid_view_id=view.grid_view_id, user_id=admin.user_id
    )
    assert row is view
    system = seed_view(session, grid, source, name="System view")
    with pytest.raises(ViewPermissionError):
        authorize_stored_view_promotion(
            session, grid_view_id=system.grid_view_id, user_id=admin.user_id
        )


# --- Stored data-source authoring gate (REQ-019) -----------------------------------


def test_data_source_authoring_needs_the_persisted_grant(session: Session) -> None:
    admin = seed_user(session, "admin.pat")
    mentor = seed_user(session, "mentor.sam")
    grant_capability(session, admin, CAP_DATA_SOURCE_AUTHOR)
    authorize_stored_data_source_authoring(session, user_id=admin.user_id)
    with pytest.raises(CapabilityError):
        authorize_stored_data_source_authoring(session, user_id=mentor.user_id)


# --- Deep-link facts and resolution over stored rows (REQ-028) ---------------------


def resolve(
    session: Session, facts: NamedViewLinkFacts, requester: AppUser
) -> OpenLinkedView | FallbackToLastUsed | LinkAccessDenied:
    """The endpoint's composition: stored facts in, the pure rule decides."""
    return resolve_grid_link(
        GridLink(
            grid_id=facts.grid_key,
            view_id=facts.view_id,
            view_owner_id=facts.view_owner_id,
        ),
        requester_id=requester.user_id,
        has_data_source_access=facts.has_data_source_access,
        last_view_preference=None,
    )


def test_system_view_link_opens_for_anyone_with_the_data_source_grant(
    session: Session,
) -> None:
    mentor = seed_user(session, "mentor.sam")
    grid, source = seed_grid_source(session)
    view = seed_view(session, grid, source)
    seed_link(session, grid, view)
    grant_data_source_role(session, data_source_key=source.data_source_key, role_name="mentor")
    facts = load_deep_link_facts(session, deep_link_key="lnk-1", user_roles=MENTOR_ROLES)
    assert facts == NamedViewLinkFacts(
        grid_key="engagements",
        view_id=view.grid_view_id,
        view_owner_id=None,
        has_data_source_access=True,
    )
    assert resolve(session, facts, mentor) == OpenLinkedView(view.grid_view_id)


def test_link_without_the_data_source_grant_is_the_standard_refusal(
    session: Session,
) -> None:
    mentor = seed_user(session, "mentor.sam")
    grid, source = seed_grid_source(session)
    view = seed_view(session, grid, source)
    seed_link(session, grid, view)
    # Deny by default: the source has no grant rows at all.
    facts = load_deep_link_facts(session, deep_link_key="lnk-1", user_roles=MENTOR_ROLES)
    assert isinstance(facts, NamedViewLinkFacts)
    assert not facts.has_data_source_access
    assert isinstance(resolve(session, facts, mentor), LinkAccessDenied)


def test_foreign_private_view_link_falls_back_with_the_notice(session: Session) -> None:
    owner = seed_user(session, "mentor.sam")
    recipient = seed_user(session, "mentor.alex")
    grid, source = seed_grid_source(session)
    view = seed_view(session, grid, source, owner=owner)
    seed_link(session, grid, view)
    grant_data_source_role(session, data_source_key=source.data_source_key, role_name="mentor")
    facts = load_deep_link_facts(session, deep_link_key="lnk-1", user_roles=MENTOR_ROLES)
    assert isinstance(facts, NamedViewLinkFacts)
    assert facts.view_owner_id == owner.user_id
    outcome = resolve(session, facts, recipient)
    assert isinstance(outcome, FallbackToLastUsed)
    assert "private view" in outcome.notice


def test_grid_only_link_and_retired_view_both_yield_grid_only_facts(
    session: Session,
) -> None:
    mentor = seed_user(session, "mentor.sam")
    grid, source = seed_grid_source(session)
    view = seed_view(session, grid, source, owner=mentor)
    link = seed_link(session, grid, None)
    facts = load_deep_link_facts(session, deep_link_key="lnk-1", user_roles=MENTOR_ROLES)
    assert facts == GridOnlyLinkFacts("engagements")
    link.grid_view_id = view.grid_view_id
    view.deleted_at = utcnow()
    session.flush()
    facts = load_deep_link_facts(session, deep_link_key="lnk-1", user_roles=MENTOR_ROLES)
    assert facts == GridOnlyLinkFacts("engagements")


def test_retired_data_source_closes_the_link(session: Session) -> None:
    grid, source = seed_grid_source(session)
    view = seed_view(session, grid, source)
    seed_link(session, grid, view)
    grant_data_source_role(session, data_source_key=source.data_source_key, role_name="mentor")
    source.deleted_at = utcnow()
    session.flush()
    facts = load_deep_link_facts(session, deep_link_key="lnk-1", user_roles=MENTOR_ROLES)
    assert isinstance(facts, NamedViewLinkFacts)
    assert not facts.has_data_source_access


def test_dead_links_raise_not_found(session: Session) -> None:
    grid, source = seed_grid_source(session)
    view = seed_view(session, grid, source)
    link = seed_link(session, grid, view)
    with pytest.raises(DeepLinkNotFoundError):
        load_deep_link_facts(session, deep_link_key="unknown", user_roles=MENTOR_ROLES)
    link.deleted_at = utcnow()
    session.flush()
    with pytest.raises(DeepLinkNotFoundError):
        load_deep_link_facts(session, deep_link_key="lnk-1", user_roles=MENTOR_ROLES)
