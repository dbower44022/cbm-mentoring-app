"""WTK-044: view/data-source permission model and persona capabilities.

REQ-017 — system views read-only (save-as-user / apply-temporarily only),
users manage exactly their own saved views, promotion is the admin sharing
path. REQ-019 — only ``adminSql.author`` holders author data sources.
REQ-028 — links are references: the data-source grant is still required and
a refusal renders the standard ``permission_refusal`` state; a foreign
private view falls back because it is invisible (:func:`view_visible_to`).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from mentorapp.access.grants import (
    InMemoryGrantRegistry,
    SourceGrant,
    roles_cover_data_source,
)
from mentorapp.access.views import (
    ADMIN_CAPABILITIES,
    CAP_DATA_SOURCE_AUTHOR,
    CAP_VIEW_PROMOTE,
    PERMISSION_REFUSAL,
    PERSONA_ADMIN,
    PERSONA_CAPABILITIES,
    PERSONA_USER,
    SAVE_AS_USER,
    SAVE_IN_PLACE,
    USER_BASELINE_CAPABILITIES,
    CapabilityError,
    InMemoryCapabilityRegistry,
    StoredCapabilityRegistry,
    ViewFacts,
    ViewPermissionError,
    authorize_capability,
    authorize_data_source_authoring,
    authorize_view_management,
    authorize_view_promotion,
    can_apply_temporarily,
    can_manage_view,
    effective_capabilities,
    holds_capability,
    persona_for,
    save_disposition,
    view_visible_to,
)
from mentorapp.api.grid_surface import (
    FallbackToLastUsed,
    GridLink,
    LinkAccessDenied,
    OpenLinkedView,
    resolve_grid_link,
)
from mentorapp.storage import AccessGrant, AppUser, utcnow

MENTOR = uuid.uuid4()
OTHER_MENTOR = uuid.uuid4()
ADMIN = uuid.uuid4()


def system_view(**overrides: object) -> ViewFacts:
    facts: dict = {"view_id": uuid.uuid4(), "owner_id": None}
    facts.update(overrides)
    return ViewFacts(**facts)


def own_view(**overrides: object) -> ViewFacts:
    facts: dict = {"view_id": uuid.uuid4(), "owner_id": MENTOR}
    facts.update(overrides)
    return ViewFacts(**facts)


# --- Persona capability map (REQ-017, REQ-019, REQ-028) --------------------------


def test_admin_persona_holds_the_user_baseline_plus_admin_capabilities() -> None:
    assert PERSONA_CAPABILITIES[PERSONA_USER] == USER_BASELINE_CAPABILITIES
    assert (
        PERSONA_CAPABILITIES[PERSONA_ADMIN] == USER_BASELINE_CAPABILITIES | ADMIN_CAPABILITIES
    )
    # Authoring and promotion are admin-only: never in the baseline.
    assert not USER_BASELINE_CAPABILITIES & ADMIN_CAPABILITIES


def test_effective_capabilities_ignore_stray_grant_keys() -> None:
    held = effective_capabilities(frozenset({"retired.capability", CAP_VIEW_PROMOTE}))
    assert CAP_VIEW_PROMOTE in held
    assert "retired.capability" not in held


def test_persona_label_needs_the_full_admin_set() -> None:
    assert persona_for(frozenset()) == PERSONA_USER
    assert persona_for(frozenset({CAP_VIEW_PROMOTE})) == PERSONA_USER
    assert persona_for(ADMIN_CAPABILITIES) == PERSONA_ADMIN


def test_partial_holder_capability_still_works_despite_user_persona() -> None:
    registry = InMemoryCapabilityRegistry({MENTOR: frozenset({CAP_VIEW_PROMOTE})})
    assert holds_capability(registry, user_id=MENTOR, capability=CAP_VIEW_PROMOTE)
    with pytest.raises(CapabilityError):
        authorize_capability(registry, user_id=MENTOR, capability=CAP_DATA_SOURCE_AUTHOR)


def test_data_source_authoring_is_admin_only(caplog: pytest.LogCaptureFixture) -> None:
    registry = InMemoryCapabilityRegistry({ADMIN: frozenset({CAP_DATA_SOURCE_AUTHOR})})
    authorize_data_source_authoring(registry, user_id=ADMIN)
    with pytest.raises(CapabilityError) as refusal:
        authorize_data_source_authoring(registry, user_id=MENTOR)
    assert refusal.value.capability == CAP_DATA_SOURCE_AUTHOR
    assert "capability refused" in caplog.text


def test_stored_capability_registry_reads_live_rows_only(session: Session) -> None:
    admin = AppUser(crm_user_id="crm-admin", username="admin.pat")
    session.add(admin)
    session.flush()
    session.add(AccessGrant(user_id=admin.user_id, access_grant_key=CAP_DATA_SOURCE_AUTHOR))
    revoked = AccessGrant(user_id=admin.user_id, access_grant_key=CAP_VIEW_PROMOTE)
    revoked.deleted_at = utcnow()
    session.add(revoked)
    session.flush()
    registry = StoredCapabilityRegistry(session)
    assert registry.grant_keys_held(admin.user_id) == frozenset({CAP_DATA_SOURCE_AUTHOR})
    assert registry.grant_keys_held(MENTOR) == frozenset()


# --- View lifecycle (REQ-017) -----------------------------------------------------


def test_system_view_modifications_can_only_be_saved_as_user() -> None:
    assert save_disposition(system_view(), user_id=MENTOR) == SAVE_AS_USER
    assert save_disposition(own_view(), user_id=MENTOR) == SAVE_IN_PLACE


def test_locked_and_foreign_and_temporary_views_never_save_in_place() -> None:
    assert save_disposition(own_view(read_only=True), user_id=MENTOR) == SAVE_AS_USER
    assert save_disposition(own_view(), user_id=OTHER_MENTOR) == SAVE_AS_USER
    assert save_disposition(own_view(temporary_modified=True), user_id=MENTOR) == SAVE_AS_USER


def test_users_manage_exactly_their_own_saved_views() -> None:
    assert can_manage_view(own_view(), user_id=MENTOR)
    assert not can_manage_view(own_view(), user_id=OTHER_MENTOR)
    assert not can_manage_view(system_view(), user_id=MENTOR)
    assert not can_manage_view(own_view(read_only=True), user_id=MENTOR)
    assert not can_manage_view(own_view(temporary_modified=True), user_id=MENTOR)


def test_view_management_refusal_is_typed_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    view = system_view()
    with pytest.raises(ViewPermissionError) as refusal:
        authorize_view_management(view, user_id=MENTOR, action="delete")
    assert refusal.value.action == "delete"
    assert refusal.value.view_id == view.view_id
    assert "view management refused" in caplog.text


def test_apply_temporarily_is_open_on_anything_visible() -> None:
    assert can_apply_temporarily(system_view(read_only=True), user_id=MENTOR)
    assert can_apply_temporarily(own_view(), user_id=MENTOR)
    assert not can_apply_temporarily(own_view(), user_id=OTHER_MENTOR)


def test_promotion_needs_the_capability_and_a_saved_user_view() -> None:
    admins = InMemoryCapabilityRegistry({ADMIN: frozenset({CAP_VIEW_PROMOTE})})
    authorize_view_promotion(own_view(), lookup=admins, user_id=ADMIN)
    with pytest.raises(CapabilityError):
        authorize_view_promotion(own_view(), lookup=admins, user_id=MENTOR)
    with pytest.raises(ViewPermissionError):
        authorize_view_promotion(system_view(), lookup=admins, user_id=ADMIN)
    with pytest.raises(ViewPermissionError):
        authorize_view_promotion(
            own_view(temporary_modified=True), lookup=admins, user_id=ADMIN
        )


# --- Deep-link gating (REQ-028): one boundary, one resolution rule ----------------

GRANTS = InMemoryGrantRegistry(
    [SourceGrant(data_source_key="engagementsForMentor", role_name="mentor")]
)


def linked(view: ViewFacts) -> GridLink:
    return GridLink(grid_id="engagements", view_id=view.view_id, view_owner_id=view.owner_id)


def gate(view: ViewFacts, *, user_id: uuid.UUID, roles: frozenset[str]) -> object:
    """The composition an endpoint performs: the REQ-006 grant boundary
    supplies the permission fact; the pure resolver decides what opens."""
    return resolve_grid_link(
        linked(view),
        requester_id=user_id,
        has_data_source_access=roles_cover_data_source(
            GRANTS, data_source_key="engagementsForMentor", user_roles=roles
        ),
        last_view_preference=None,
    )


def test_unpermitted_link_renders_the_standard_permission_refusal_state() -> None:
    outcome = gate(system_view(), user_id=MENTOR, roles=frozenset({"volunteer"}))
    assert isinstance(outcome, LinkAccessDenied)
    assert PERMISSION_REFUSAL == "permission_refusal"


def test_system_view_link_opens_for_any_permitted_user() -> None:
    view = system_view()
    outcome = gate(view, user_id=MENTOR, roles=frozenset({"mentor"}))
    assert outcome == OpenLinkedView(view.view_id)


def test_foreign_private_view_is_invisible_so_the_link_falls_back() -> None:
    view = own_view()
    assert not view_visible_to(view, OTHER_MENTOR)
    outcome = gate(view, user_id=OTHER_MENTOR, roles=frozenset({"mentor"}))
    assert isinstance(outcome, FallbackToLastUsed)
    assert "private view" in outcome.notice
