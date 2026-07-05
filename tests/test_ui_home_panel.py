"""Home panel & admin messaging design gate: frame, startup, dashlets, messages (WTK-019)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mentorapp.ui import (
    ACCOUNT_MENU,
    DEFAULT_STARTUP_CHOICE,
    HOME_FRAME,
    HOME_PANEL_KEY,
    MESSAGES_DASHLET,
    STARTUP_FALLBACK_TO_HOME,
    AcknowledgmentNotRequestedError,
    AdminMessage,
    DashletRef,
    MessageCenter,
    MessagePriority,
    StartupChoice,
    UnknownMessageError,
    resolve_home_dashlets,
    resolve_startup_panel,
)

T0 = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)


def _message(key: str, *, minutes_ago: int = 0, **overrides: object) -> AdminMessage:
    defaults: dict[str, object] = {
        "title": f"Message {key}",
        "body": "Body.",
        "posted_by": "admin",
        "posted_at": T0 - timedelta(minutes=minutes_ago),
    }
    defaults.update(overrides)
    return AdminMessage(key=key, **defaults)  # type: ignore[arg-type]


# --- The home frame (REQ-003) --------------------------------------------------


def test_frame_places_logo_identity_and_areas_per_req_003() -> None:
    assert HOME_FRAME.logo_zone == "upperLeft"
    assert HOME_FRAME.identity_zone == "upperRight"
    assert HOME_FRAME.areas_zone == "leftEdge"
    assert HOME_FRAME.header_right == ("notificationBell", "help", "accountMenu")


def test_account_menu_covers_preferences_and_ends_with_help() -> None:
    keys = [item.key for item in ACCOUNT_MENU]
    assert {"navigationStyle", "startup", "themes", "manageViewsAndPins", "logout"} <= set(keys)
    # The app-wide rule: the last item in every menu is Help.
    assert keys[-1] == "help"


def test_areas_rail_lists_permissioned_panels_and_may_be_empty() -> None:
    assert HOME_FRAME.areas_for(["home", "mentees", "sessions"]) == ("mentees", "sessions")
    assert HOME_FRAME.areas_for(["home"]) == ()


# --- Startup preference ---------------------------------------------------------


def test_startup_defaults_to_home() -> None:
    assert DEFAULT_STARTUP_CHOICE is StartupChoice.HOME
    target = resolve_startup_panel(StartupChoice.HOME, "mentees", {"mentees"})
    assert target.panel_key == HOME_PANEL_KEY
    assert target.notice is None


def test_startup_honors_last_panel_when_it_is_still_openable() -> None:
    target = resolve_startup_panel(StartupChoice.LAST_PANEL, "mentees", {"mentees"})
    assert target.panel_key == "mentees"
    assert target.notice is None


def test_startup_first_login_lands_home_without_a_notice() -> None:
    # No recorded last panel is a normal state, not a broken pin.
    target = resolve_startup_panel(StartupChoice.LAST_PANEL, None, {"mentees"})
    assert target.panel_key == HOME_PANEL_KEY
    assert target.notice is None


def test_startup_broken_last_panel_lands_home_with_the_explanation() -> None:
    target = resolve_startup_panel(StartupChoice.LAST_PANEL, "retired", {"mentees"})
    assert target.panel_key == HOME_PANEL_KEY
    assert target.notice == STARTUP_FALLBACK_TO_HOME
    assert "Home" in STARTUP_FALLBACK_TO_HOME.what_next


# --- Dashlets --------------------------------------------------------------------


def test_home_leads_with_the_admin_messages_dashlet() -> None:
    resolved = resolve_home_dashlets([], set())
    assert resolved == (MESSAGES_DASHLET,)
    assert MESSAGES_DASHLET.notice is None


def test_broken_dashlets_stay_visible_with_an_explanation() -> None:
    chosen = [DashletRef("views.due", "Due this week"), DashletRef("views.gone", "Old view")]
    resolved = resolve_home_dashlets(chosen, {"views.due"})
    assert [d.view_key for d in resolved] == ["home.messages", "views.due", "views.gone"]
    assert resolved[1].notice is None
    broken = resolved[2].notice
    assert broken is not None
    assert "Old view" in broken.what_happened


# --- Admin messaging --------------------------------------------------------------


def test_messages_render_newest_first_and_expired_ones_drop_out() -> None:
    center = MessageCenter()
    center.post(_message("old", minutes_ago=60))
    center.post(_message("new", minutes_ago=5))
    center.post(_message("expired", minutes_ago=90, expires_at=T0 - timedelta(minutes=1)))
    assert [m.key for m in center.visible_messages(T0)] == ["new", "old"]


def test_viewing_home_reads_what_it_displays() -> None:
    center = MessageCenter()
    center.post(_message("a", minutes_ago=10))
    center.post(_message("b", minutes_ago=5))
    assert center.unread_count("mentor-1", T0) == 2

    shown = center.view_home("mentor-1", T0)

    assert [m.key for m in shown] == ["b", "a"]
    assert center.unread_count("mentor-1", T0) == 0
    # Read state is per-user: another user's badge is untouched.
    assert center.unread_count("mentor-2", T0) == 2


def test_expired_messages_are_neither_shown_nor_read() -> None:
    center = MessageCenter()
    center.post(_message("gone", expires_at=T0 - timedelta(minutes=1)))
    assert center.view_home("mentor-1", T0) == ()
    assert center.unread_count("mentor-1", T0) == 0


def test_urgent_banner_shows_on_every_panel_until_read() -> None:
    center = MessageCenter()
    center.post(_message("urgent", priority=MessagePriority.URGENT))
    center.post(_message("normal"))

    banner = center.urgent_banner("mentor-1", T0)
    assert [m.key for m in banner] == ["urgent"]

    center.view_home("mentor-1", T0)
    assert center.urgent_banner("mentor-1", T0) == ()
    assert center.urgent_banner("mentor-2", T0) != ()


def test_acknowledgment_is_explicit_and_reading_never_acknowledges() -> None:
    center = MessageCenter()
    center.post(_message("policy", requires_acknowledgment=True))
    users = ["mentor-1", "mentor-2"]

    center.view_home("mentor-1", T0)
    assert center.outstanding_acknowledgments("policy", users) == ("mentor-1", "mentor-2")

    center.acknowledge("mentor-1", "policy")
    assert center.outstanding_acknowledgments("policy", users) == ("mentor-2",)


def test_urgent_banner_clears_on_read_even_when_acknowledgment_is_pending() -> None:
    # The banner guarantees delivery; the acknowledgment report covers consent.
    center = MessageCenter()
    center.post(_message("evac", priority=MessagePriority.URGENT, requires_acknowledgment=True))
    center.view_home("mentor-1", T0)
    assert center.urgent_banner("mentor-1", T0) == ()
    assert center.outstanding_acknowledgments("evac", ["mentor-1"]) == ("mentor-1",)


def test_acknowledging_implies_having_read() -> None:
    center = MessageCenter()
    center.post(
        _message("policy", priority=MessagePriority.URGENT, requires_acknowledgment=True)
    )
    center.acknowledge("mentor-1", "policy")
    assert center.unread_count("mentor-1", T0) == 0
    assert center.urgent_banner("mentor-1", T0) == ()


def test_acknowledgment_misuse_is_loud() -> None:
    center = MessageCenter()
    center.post(_message("plain"))
    with pytest.raises(UnknownMessageError):
        center.acknowledge("mentor-1", "missing")
    with pytest.raises(AcknowledgmentNotRequestedError):
        center.acknowledge("mentor-1", "plain")
    with pytest.raises(AcknowledgmentNotRequestedError):
        center.outstanding_acknowledgments("plain", ["mentor-1"])


def test_expiration_does_not_close_the_acknowledgment_books() -> None:
    center = MessageCenter()
    center.post(
        _message("audit", requires_acknowledgment=True, expires_at=T0 - timedelta(minutes=1))
    )
    assert center.visible_messages(T0) == ()
    assert center.outstanding_acknowledgments("audit", ["mentor-1"]) == ("mentor-1",)
