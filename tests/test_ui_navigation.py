"""WTK-020: navigation presentations, pins, and the broken-pin fallback.

REQ-010 — switching among the three presentations keeps the pin set intact,
and a pin opens its panel with its view active, permissioned by the panel's
data source. REQ-015 — an unavailable pin stays, marked and explained (what,
by whom, when), and startup/deep links fall back to Home with the same
explanation instead of a blank screen.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from mentorapp.access.grants import InMemoryGrantRegistry, SourceGrant
from mentorapp.ui.navigation import (
    DEFAULT_PRESENTATION,
    HOME_PANEL,
    HOME_PANEL_KEY,
    InMemoryNavigationCatalog,
    NavigationPresentation,
    NavigationProfile,
    Panel,
    PanelType,
    Pin,
    PinBreakReason,
    ResolvedPin,
    ViewRecord,
    broken_pin_fallback,
    navigation_preference_document,
    navigation_profile_from_document,
    resolve_pin,
    resolve_startup_target,
    switch_navigation_presentation,
)

USER_ID = uuid.uuid4()
MENTOR_ROLES = frozenset({"mentor"})

ENGAGEMENTS_PANEL = Panel(
    panel_key="engagements",
    title="Engagements",
    panel_type=PanelType.GRID,
    data_source_key="engagementsForMentor",
)
FOLLOW_UP_VIEW = ViewRecord(
    view_key="needsFollowUp", name="Needs follow-up", panel_key="engagements"
)
FOLLOW_UP_PIN = Pin(
    pin_key="pin-1",
    panel_key="engagements",
    view_key="needsFollowUp",
    label="Needs follow-up",
)


def make_catalog() -> InMemoryNavigationCatalog:
    return InMemoryNavigationCatalog(
        panels={"engagements": ENGAGEMENTS_PANEL, HOME_PANEL_KEY: HOME_PANEL},
        views={"needsFollowUp": FOLLOW_UP_VIEW},
    )


def make_grants() -> InMemoryGrantRegistry:
    return InMemoryGrantRegistry([SourceGrant("engagementsForMentor", "mentor")])


def resolve(
    pin: Pin, catalog: InMemoryNavigationCatalog, grants: InMemoryGrantRegistry
) -> ResolvedPin:
    return resolve_pin(
        pin, catalog=catalog, grants=grants, user_id=USER_ID, user_roles=MENTOR_ROLES
    )


# --- REQ-010: presentation switching preserves pins ------------------------------


def test_switching_presentation_carries_the_same_pin_set() -> None:
    profile = NavigationProfile(presentation=NavigationPresentation.TABS, pins=(FOLLOW_UP_PIN,))
    for presentation in NavigationPresentation:
        switched = switch_navigation_presentation(profile, presentation)
        assert switched.presentation is presentation
        assert switched.pins is profile.pins


def test_preference_document_round_trips_the_whole_profile() -> None:
    profile = NavigationProfile(
        presentation=NavigationPresentation.GROUP_TREE,
        pins=(FOLLOW_UP_PIN, Pin("pin-2", "home", "myDay", "My day", group="Personal")),
    )
    document = navigation_preference_document(profile)
    assert navigation_profile_from_document(document) == profile


def test_document_parsing_degrades_instead_of_failing() -> None:
    document = {
        "presentation": "hologram",
        "pins": [
            {"pinKey": "p", "panelKey": "engagements", "viewKey": "v", "label": "Ok"},
            {"pinKey": "broken"},
            "not-a-dict-either",
        ],
    }
    profile = navigation_profile_from_document(document)
    assert profile.presentation is DEFAULT_PRESENTATION
    assert [pin.label for pin in profile.pins] == ["Ok"]
    assert navigation_profile_from_document({}) == NavigationProfile()


# --- REQ-010: pins reference views; panel permission is data-source permission ---


def test_healthy_pin_resolves_to_its_panel_and_view() -> None:
    resolved = resolve(FOLLOW_UP_PIN, make_catalog(), make_grants())
    assert not resolved.is_broken
    assert resolved.pin is FOLLOW_UP_PIN


def test_panel_without_data_source_needs_no_grant() -> None:
    catalog = make_catalog()
    catalog.views["myDay"] = ViewRecord(
        view_key="myDay", name="My day", panel_key=HOME_PANEL_KEY
    )
    pin = Pin("pin-2", HOME_PANEL_KEY, "myDay", "My day")
    assert not resolve(pin, catalog, InMemoryGrantRegistry()).is_broken


def test_ungranted_data_source_breaks_the_pin_but_keeps_it() -> None:
    resolved = resolve(FOLLOW_UP_PIN, make_catalog(), InMemoryGrantRegistry())
    assert resolved.is_broken
    assert resolved.break_ is not None
    assert resolved.break_.reason is PinBreakReason.ACCESS_REVOKED
    message = broken_pin_fallback(FOLLOW_UP_PIN, resolved.break_)
    assert "engagementsForMentor" in message.why
    assert "administrator" in message.why


# --- REQ-015: broken pins are marked and explained, never removed ----------------


def test_soft_deleted_view_yields_who_and_when_in_the_explanation() -> None:
    catalog = make_catalog()
    catalog.views["needsFollowUp"] = ViewRecord(
        view_key="needsFollowUp",
        name="Needs follow-up",
        panel_key="engagements",
        deleted_at=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        deleted_by="Dana (admin)",
    )
    resolved = resolve(FOLLOW_UP_PIN, catalog, make_grants())
    assert resolved.is_broken
    assert resolved.break_ is not None
    assert resolved.break_.reason is PinBreakReason.VIEW_REMOVED
    message = broken_pin_fallback(FOLLOW_UP_PIN, resolved.break_)
    assert "Needs follow-up" in message.why
    assert "Dana (admin)" in message.why
    assert "2026-07-01" in message.why
    # Soft deletes system-wide: the way back is restore, not mourning.
    assert "restore" in message.what_next
    assert "pin" in message.what_next


def test_unknown_view_still_explains_without_inventing_facts() -> None:
    catalog = make_catalog()
    del catalog.views["needsFollowUp"]
    resolved = resolve(FOLLOW_UP_PIN, catalog, make_grants())
    assert resolved.break_ is not None
    assert resolved.break_.reason is PinBreakReason.VIEW_REMOVED
    message = broken_pin_fallback(FOLLOW_UP_PIN, resolved.break_)
    # No tombstone reached the catalog, so no actor/date may be claimed.
    assert message.why == "The view 'Needs follow-up' was removed."
    assert message.what_happened.startswith("The pin 'Needs follow-up'")


def test_missing_panel_breaks_the_pin() -> None:
    catalog = make_catalog()
    del catalog.panels["engagements"]
    resolved = resolve(FOLLOW_UP_PIN, catalog, make_grants())
    assert resolved.break_ is not None
    assert resolved.break_.reason is PinBreakReason.PANEL_REMOVED


# --- REQ-015: startup and deep links fall back to Home with the explanation ------


def test_healthy_startup_target_opens_as_asked() -> None:
    resolution = resolve_startup_target(
        FOLLOW_UP_PIN,
        catalog=make_catalog(),
        grants=make_grants(),
        user_id=USER_ID,
        user_roles=MENTOR_ROLES,
    )
    assert resolution.panel_key == "engagements"
    assert resolution.view_key == "needsFollowUp"
    assert resolution.notice is None


def test_unavailable_startup_target_lands_on_home_with_the_explanation() -> None:
    resolution = resolve_startup_target(
        FOLLOW_UP_PIN,
        catalog=make_catalog(),
        grants=InMemoryGrantRegistry(),
        user_id=USER_ID,
        user_roles=MENTOR_ROLES,
    )
    assert resolution.panel_key == HOME_PANEL_KEY
    assert resolution.view_key is None
    assert resolution.notice is not None
    assert "can't open" in resolution.notice.what_happened
