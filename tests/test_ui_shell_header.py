"""WTK-026: the standard header, quick-open palette, and logout.

REQ-009 — every window carries the one thin header (pop-outs omit
navigation but keep the right side and user menu); the user menu is the
single home for per-user preferences including logout; Ctrl+K opens a
type-ahead palette over every panel and view the user can reach.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from mentorapp.access.grants import InMemoryGrantRegistry, SourceGrant
from mentorapp.ui.auth_flows import (
    InMemorySessionChannel,
    WindowPhase,
    WindowSessionController,
)
from mentorapp.ui.home_panel import ACCOUNT_MENU, HOME_FRAME
from mentorapp.ui.navigation import HOME_PANEL, Panel, PanelType, ViewRecord
from mentorapp.ui.record_preview import POP_OUT_HEADER_RIGHT
from mentorapp.ui.shell_header import (
    LOGOUT_UNSAVED_WORK,
    MAIN_WINDOW_HEADER,
    POP_OUT_HEADER,
    QUICK_OPEN_SHORTCUT,
    QuickOpenEntry,
    QuickOpenKind,
    header_for_window,
    quick_open_entries,
    request_logout,
    search_quick_open,
)

USER_ID = uuid.uuid4()
MENTOR_ROLES = frozenset({"mentor"})

ENGAGEMENTS = Panel(
    panel_key="engagements",
    title="Engagements",
    panel_type=PanelType.GRID,
    data_source_key="engagementsForMentor",
)
ADMIN_AUDIT = Panel(
    panel_key="adminAudit",
    title="Admin audit",
    panel_type=PanelType.GRID,
    data_source_key="adminAuditLog",
)
FOLLOW_UP = ViewRecord(
    view_key="needsFollowUp", name="Needs follow-up", panel_key="engagements"
)
ARCHIVED = ViewRecord(
    view_key="archived",
    name="Archived engagements",
    panel_key="engagements",
    deleted_at=datetime(2026, 7, 1, tzinfo=UTC),
    deleted_by="Pat Admin",
)
AUDIT_VIEW = ViewRecord(view_key="auditAll", name="All audit entries", panel_key="adminAudit")


def make_grants() -> InMemoryGrantRegistry:
    return InMemoryGrantRegistry([SourceGrant("engagementsForMentor", "mentor")])


def mentor_entries() -> tuple[QuickOpenEntry, ...]:
    return quick_open_entries(
        [ENGAGEMENTS, ADMIN_AUDIT, HOME_PANEL],
        [FOLLOW_UP, ARCHIVED, AUDIT_VIEW],
        grants=make_grants(),
        user_id=USER_ID,
        user_roles=MENTOR_ROLES,
    )


# --- REQ-009: the header on every window -----------------------------------------


def test_main_window_header_carries_identity_and_navigation() -> None:
    assert MAIN_WINDOW_HEADER.left == ("identity", "navigation")
    assert header_for_window(is_pop_out=False) is MAIN_WINDOW_HEADER


def test_pop_out_header_omits_navigation_but_keeps_the_right_side() -> None:
    assert POP_OUT_HEADER.left == ("identity",)
    assert POP_OUT_HEADER.right == MAIN_WINDOW_HEADER.right
    assert header_for_window(is_pop_out=True) is POP_OUT_HEADER


def test_header_zones_are_the_home_frame_rulings_by_reference() -> None:
    # One canonical home per concept: the header must carry WTK-019's right
    # side and account menu, and match what WTK-021 gave pop-outs.
    assert MAIN_WINDOW_HEADER.right == HOME_FRAME.header_right
    assert MAIN_WINDOW_HEADER.account_menu == ACCOUNT_MENU
    assert POP_OUT_HEADER.right == POP_OUT_HEADER_RIGHT


def test_user_menu_holds_every_ruled_preference_and_logout_with_help_last() -> None:
    keys = [item.key for item in MAIN_WINDOW_HEADER.account_menu]
    for required in ("navigationStyle", "startup", "themes", "manageViewsAndPins", "logout"):
        assert required in keys
    assert keys[-1] == "help"  # the last item in EVERY menu is Help


def test_quick_open_shortcut_is_ctrl_k_on_every_window_kind() -> None:
    assert QUICK_OPEN_SHORTCUT == "Ctrl+K"
    assert MAIN_WINDOW_HEADER.quick_open_shortcut == QUICK_OPEN_SHORTCUT
    assert POP_OUT_HEADER.quick_open_shortcut == QUICK_OPEN_SHORTCUT


# --- REQ-009: the palette lists exactly what the user can reach -------------------


def test_palette_lists_permitted_panels_and_views_panels_first() -> None:
    entries = mentor_entries()
    assert entries == (
        QuickOpenEntry(QuickOpenKind.PANEL, "Engagements", "engagements"),
        QuickOpenEntry(QuickOpenKind.PANEL, "Home", "home"),
        QuickOpenEntry(QuickOpenKind.VIEW, "Needs follow-up", "engagements", "needsFollowUp"),
    )


def test_palette_excludes_unpermitted_panels_and_their_views() -> None:
    labels = [e.label for e in mentor_entries()]
    assert "Admin audit" not in labels
    assert "All audit entries" not in labels


def test_palette_excludes_soft_deleted_views() -> None:
    assert "Archived engagements" not in [e.label for e in mentor_entries()]


def test_home_needs_no_grant_to_appear() -> None:
    entries = quick_open_entries(
        [HOME_PANEL],
        [],
        grants=InMemoryGrantRegistry(),
        user_id=USER_ID,
        user_roles=frozenset(),
    )
    assert entries == (QuickOpenEntry(QuickOpenKind.PANEL, "Home", "home"),)


def test_view_whose_panel_is_not_in_the_catalog_is_unreachable() -> None:
    entries = quick_open_entries(
        [HOME_PANEL],
        [FOLLOW_UP],
        grants=make_grants(),
        user_id=USER_ID,
        user_roles=MENTOR_ROLES,
    )
    assert [e.label for e in entries] == ["Home"]


# --- Type-ahead search -------------------------------------------------------------


def test_blank_query_presents_the_full_catalog() -> None:
    entries = mentor_entries()
    assert search_quick_open(entries, "   ") == entries


def test_search_matches_substrings_case_insensitively() -> None:
    entries = mentor_entries()
    assert [e.label for e in search_quick_open(entries, "FOLLOW")] == ["Needs follow-up"]


def test_prefix_matches_rank_before_substring_matches() -> None:
    entries = (
        QuickOpenEntry(QuickOpenKind.PANEL, "Mentor sessions", "sessions"),
        QuickOpenEntry(QuickOpenKind.PANEL, "Sessions archive", "archive"),
    )
    assert [e.label for e in search_quick_open(entries, "ses")] == [
        "Sessions archive",
        "Mentor sessions",
    ]


def test_no_match_returns_empty() -> None:
    assert search_quick_open(mentor_entries(), "zzz") == ()


# --- Logout from the user menu ------------------------------------------------------


def test_logout_ends_the_session_totally_across_windows() -> None:
    channel = InMemorySessionChannel()
    window = WindowSessionController(channel, "ref-1")
    sibling = WindowSessionController(channel, "ref-1")
    assert request_logout(window) is None
    assert window.phase is WindowPhase.ENDED
    assert sibling.phase is WindowPhase.ENDED


def test_logout_over_unsaved_work_returns_the_dirty_guard_prompt() -> None:
    channel = InMemorySessionChannel()
    window = WindowSessionController(channel, "ref-1")
    window.unsaved_work["notes"] = "half-written session note"
    assert request_logout(window) is LOGOUT_UNSAVED_WORK
    assert window.phase is WindowPhase.ACTIVE  # still signed in; nothing discarded


def test_confirmed_discard_logs_out_anyway() -> None:
    channel = InMemorySessionChannel()
    window = WindowSessionController(channel, "ref-1")
    window.unsaved_work["notes"] = "half-written session note"
    assert request_logout(window, discard_confirmed=True) is None
    assert window.phase is WindowPhase.ENDED
