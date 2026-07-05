"""WTK-025: user-to-area access derived from the grant boundary.

REQ-003 — the Areas rail shows exactly the user's permissioned areas, driven
by :func:`accessible_area_keys` over the REQ-006 grants; a Home-only user gets
an empty rail, never an error. REQ-015 — the same derivation feeds the startup
fallback, so a revoked grant sends startup to Home with the explanation. One
boundary: granting or revoking a role changes the rail and the fallbacks
together, with no second per-user assignment to update.
"""

from __future__ import annotations

import uuid

import pytest

from mentorapp.access.areas import (
    AreaDescriptor,
    accessible_area_keys,
    authorize_area,
    is_area_accessible,
)
from mentorapp.access.grants import (
    DataSourceAccessError,
    InMemoryGrantRegistry,
    SourceGrant,
)
from mentorapp.ui.home_panel import HOME_FRAME, StartupChoice, resolve_startup_panel

USER_ID = uuid.uuid4()
MENTOR_ROLES = frozenset({"mentor"})

HOME_AREA = AreaDescriptor(area_key="home")
ENGAGEMENTS_AREA = AreaDescriptor(
    area_key="engagements", data_source_key="engagementsForMentor"
)
ADMIN_AREA = AreaDescriptor(area_key="administration", data_source_key="adminAudit")

AREAS = (HOME_AREA, ENGAGEMENTS_AREA, ADMIN_AREA)


def make_grants() -> InMemoryGrantRegistry:
    return InMemoryGrantRegistry(
        [
            SourceGrant(data_source_key="engagementsForMentor", role_name="mentor"),
            SourceGrant(data_source_key="adminAudit", role_name="administrator"),
        ]
    )


# --- The derivation itself -------------------------------------------------------


def test_sourceless_area_is_open_to_every_signed_in_user() -> None:
    assert is_area_accessible(HOME_AREA, grants=make_grants(), user_roles=frozenset())


def test_area_access_follows_the_data_source_grant() -> None:
    grants = make_grants()
    assert is_area_accessible(ENGAGEMENTS_AREA, grants=grants, user_roles=MENTOR_ROLES)
    assert not is_area_accessible(ADMIN_AREA, grants=grants, user_roles=MENTOR_ROLES)


def test_accessible_area_keys_filters_in_caller_order() -> None:
    keys = accessible_area_keys(
        AREAS, grants=make_grants(), user_id=USER_ID, user_roles=MENTOR_ROLES
    )
    assert keys == ("home", "engagements")


def test_home_only_user_still_gets_home() -> None:
    keys = accessible_area_keys(
        AREAS, grants=make_grants(), user_id=USER_ID, user_roles=frozenset({"guest"})
    )
    assert keys == ("home",)


def test_revoking_the_grant_changes_the_derivation_on_the_next_ask() -> None:
    grants = InMemoryGrantRegistry()
    assert accessible_area_keys(
        AREAS, grants=grants, user_id=USER_ID, user_roles=MENTOR_ROLES
    ) == ("home",)
    grants.add(SourceGrant(data_source_key="engagementsForMentor", role_name="mentor"))
    assert accessible_area_keys(
        AREAS, grants=grants, user_id=USER_ID, user_roles=MENTOR_ROLES
    ) == ("home", "engagements")


# --- Panel permissioning: the attempt form ----------------------------------------


def test_authorize_area_passes_home_unconditionally() -> None:
    authorize_area(HOME_AREA, grants=make_grants(), user_id=USER_ID, user_roles=frozenset())


def test_authorize_area_raises_the_req006_error_for_an_ungranted_area() -> None:
    with pytest.raises(DataSourceAccessError):
        authorize_area(
            ADMIN_AREA, grants=make_grants(), user_id=USER_ID, user_roles=MENTOR_ROLES
        )


# --- The derivation drives the shipped REQ-003 / REQ-015 surfaces -----------------


def test_derivation_drives_the_home_areas_rail() -> None:
    keys = accessible_area_keys(
        AREAS, grants=make_grants(), user_id=USER_ID, user_roles=MENTOR_ROLES
    )
    assert HOME_FRAME.areas_for(keys) == ("engagements",)


def test_derivation_drives_the_startup_fallback_after_revocation() -> None:
    keys = accessible_area_keys(
        AREAS, grants=InMemoryGrantRegistry(), user_id=USER_ID, user_roles=MENTOR_ROLES
    )
    target = resolve_startup_panel(StartupChoice.LAST_PANEL, "engagements", keys)
    assert target.panel_key == "home"
    assert target.notice is not None
