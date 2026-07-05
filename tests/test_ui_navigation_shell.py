"""WTK-028: navigation rendering, pin opening, and the broken-pin dialog.

REQ-010 — the three presentations are renderings of the one pin set (switching
preserves membership and order), and activating a pin opens its panel with the
referenced view active. REQ-015 — activating a broken pin opens the explanation
dialog offering exactly remove-or-reselect, and applying either choice edits
the profile explicitly (never silently).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from mentorapp.access.grants import InMemoryGrantRegistry, SourceGrant
from mentorapp.ui.navigation import (
    HOME_PANEL,
    HOME_PANEL_KEY,
    BrokenPinChoice,
    InMemoryNavigationCatalog,
    NavigationPresentation,
    NavigationProfile,
    Panel,
    PanelType,
    Pin,
    PinBreakReason,
    ViewRecord,
    navigation_preference_document,
    navigation_profile_from_document,
    switch_navigation_presentation,
)
from mentorapp.ui.navigation_shell import (
    BrokenPinDialog,
    PanelOpening,
    UnknownPinError,
    open_pin,
    remove_pin,
    render_navigation,
    repoint_pin,
    resolve_navigation,
)

USER_ID = uuid.uuid4()
MENTOR_ROLES = frozenset({"mentor"})

ENGAGEMENTS_PANEL = Panel(
    panel_key="engagements",
    title="Engagements",
    panel_type=PanelType.GRID,
    data_source_key="engagementsForMentor",
)
FOLLOW_UP_PIN = Pin(
    pin_key="pin-1",
    panel_key="engagements",
    view_key="needsFollowUp",
    label="Needs follow-up",
    group="Work",
)
MY_DAY_PIN = Pin(pin_key="pin-2", panel_key=HOME_PANEL_KEY, view_key="myDay", label="My day")
THIS_WEEK_PIN = Pin(
    pin_key="pin-3",
    panel_key="engagements",
    view_key="thisWeek",
    label="This week",
    group="Work",
)
PROFILE = NavigationProfile(
    presentation=NavigationPresentation.TABS,
    pins=(FOLLOW_UP_PIN, MY_DAY_PIN, THIS_WEEK_PIN),
)


def make_catalog() -> InMemoryNavigationCatalog:
    return InMemoryNavigationCatalog(
        panels={"engagements": ENGAGEMENTS_PANEL, HOME_PANEL_KEY: HOME_PANEL},
        views={
            "needsFollowUp": ViewRecord(
                view_key="needsFollowUp", name="Needs follow-up", panel_key="engagements"
            ),
            "myDay": ViewRecord(view_key="myDay", name="My day", panel_key=HOME_PANEL_KEY),
            "thisWeek": ViewRecord(
                view_key="thisWeek", name="This week", panel_key="engagements"
            ),
        },
    )


def make_grants() -> InMemoryGrantRegistry:
    return InMemoryGrantRegistry([SourceGrant("engagementsForMentor", "mentor")])


def resolve(profile: NavigationProfile, grants: InMemoryGrantRegistry):
    return resolve_navigation(
        profile,
        catalog=make_catalog(),
        grants=grants,
        user_id=USER_ID,
        user_roles=MENTOR_ROLES,
    )


# --- REQ-010: three renderings of the one pin set ---------------------------------


def test_flat_presentations_render_every_pin_in_order_ignoring_groups() -> None:
    resolved = resolve(PROFILE, make_grants())
    for presentation in (NavigationPresentation.TABS, NavigationPresentation.SIDE_MENU):
        rendering = render_navigation(presentation, resolved)
        assert len(rendering.groups) == 1
        assert rendering.groups[0].label is None
        assert [i.pin_key for i in rendering.groups[0].items] == ["pin-1", "pin-2", "pin-3"]


def test_group_tree_buckets_by_first_appearance_keeping_every_pin() -> None:
    resolved = resolve(PROFILE, make_grants())
    rendering = render_navigation(NavigationPresentation.GROUP_TREE, resolved)
    assert [g.label for g in rendering.groups] == ["Work", None]
    assert [i.pin_key for i in rendering.groups[0].items] == ["pin-1", "pin-3"]
    assert [i.pin_key for i in rendering.groups[1].items] == ["pin-2"]


def test_switching_presentation_renders_the_same_membership() -> None:
    resolved = resolve(PROFILE, make_grants())
    keys = {
        i.pin_key
        for presentation in NavigationPresentation
        for g in render_navigation(
            switch_navigation_presentation(PROFILE, presentation).presentation, resolved
        ).groups
        for i in g.items
    }
    assert keys == {"pin-1", "pin-2", "pin-3"}


def test_broken_pins_render_marked_not_dropped() -> None:
    # No grants: both engagements pins break; all three still render.
    resolved = resolve(PROFILE, InMemoryGrantRegistry())
    rendering = render_navigation(NavigationPresentation.TABS, resolved)
    broken = {i.pin_key: i.is_broken for i in rendering.groups[0].items}
    assert broken == {"pin-1": True, "pin-2": False, "pin-3": True}


# --- REQ-010: opening a pin opens its panel with the referenced view active -------


def test_healthy_pin_opens_panel_with_the_referenced_view_active() -> None:
    opened = open_pin(
        FOLLOW_UP_PIN,
        catalog=make_catalog(),
        grants=make_grants(),
        user_id=USER_ID,
        user_roles=MENTOR_ROLES,
    )
    assert opened == PanelOpening(panel_key="engagements", view_key="needsFollowUp")


# --- REQ-015: the broken-pin dialog offers exactly remove-or-reselect -------------


def test_broken_pin_opens_the_explanation_dialog_with_both_choices() -> None:
    dialog = open_pin(
        FOLLOW_UP_PIN,
        catalog=make_catalog(),
        grants=InMemoryGrantRegistry(),
        user_id=USER_ID,
        user_roles=MENTOR_ROLES,
    )
    assert isinstance(dialog, BrokenPinDialog)
    assert dialog.break_.reason is PinBreakReason.ACCESS_REVOKED
    assert dialog.choices == (
        BrokenPinChoice.REMOVE_PIN,
        BrokenPinChoice.CHOOSE_DIFFERENT_VIEW,
    )
    assert "can't open" in dialog.message.what_happened
    assert "engagementsForMentor" in dialog.message.why


def test_remove_pin_removes_only_that_pin() -> None:
    edited = remove_pin(PROFILE, "pin-1")
    assert [p.pin_key for p in edited.pins] == ["pin-2", "pin-3"]
    assert edited.presentation is PROFILE.presentation
    # The edit persists through the one preference seam and nothing else changed.
    assert navigation_profile_from_document(navigation_preference_document(edited)) == edited


def test_remove_pin_surfaces_a_cross_window_race() -> None:
    with pytest.raises(UnknownPinError):
        remove_pin(remove_pin(PROFILE, "pin-1"), "pin-1")


def test_repoint_pin_changes_target_and_label_keeping_identity_and_place() -> None:
    replacement = make_catalog().views["thisWeek"]
    edited = repoint_pin(PROFILE, "pin-1", replacement)
    assert [p.pin_key for p in edited.pins] == ["pin-1", "pin-2", "pin-3"]
    repointed = edited.pins[0]
    assert repointed.view_key == "thisWeek"
    assert repointed.panel_key == "engagements"
    assert repointed.label == "This week"
    assert repointed.group == FOLLOW_UP_PIN.group


def test_repoint_pin_refuses_a_soft_deleted_replacement() -> None:
    removed = ViewRecord(
        view_key="old",
        name="Old",
        panel_key="engagements",
        deleted_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="removed"):
        repoint_pin(PROFILE, "pin-1", removed)


def test_repointed_pin_opens_healthy_again() -> None:
    edited = repoint_pin(PROFILE, "pin-1", make_catalog().views["thisWeek"])
    opened = open_pin(
        edited.pins[0],
        catalog=make_catalog(),
        grants=make_grants(),
        user_id=USER_ID,
        user_roles=MENTOR_ROLES,
    )
    assert opened == PanelOpening(panel_key="engagements", view_key="thisWeek")
