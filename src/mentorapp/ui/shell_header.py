"""Standard header, quick-open palette, and logout (WTK-026).

The UI-layer design for REQ-009 (standard application header). No frontend
shell exists yet (PI-002), so — like its siblings — the design is executable
surface the shell renders verbatim:

- **One canonical header, composed not re-declared.** The right side
  (notification bell, Help, account menu) and the account menu itself were
  ruled in the home frame (WTK-019); :class:`StandardHeader` references
  ``HOME_FRAME.header_right`` and :data:`ACCOUNT_MENU` so the concepts keep
  their one home. :data:`MAIN_WINDOW_HEADER` carries navigation;
  :data:`POP_OUT_HEADER` omits it — pop-outs are record windows, not panel
  hosts (WTK-021 pinned the same rule as ``POP_OUT_HAS_NAVIGATION``).
- **The Ctrl+K palette lists destinations, not actions.** The never-hide
  rule governs actions; the palette is type-ahead over "every panel and view
  the user can reach" (REQ-009), so :func:`quick_open_entries` filters by
  the one grant boundary — :func:`roles_cover_data_source`, the quiet form,
  because composing a listing is not an audit-relevant open attempt — and
  soft-deleted views simply are not reachable destinations.
- **Search is a narrowing type-ahead.** :func:`search_quick_open` matches
  case-insensitive substrings of the label; prefix matches rank first, ties
  alphabetical, and an empty query presents everything (the palette doubles
  as a full catalog of where the user can go).
- **Logout stays owned by the session controller.** The user-menu Log out
  item delegates to ``WindowSessionController.logout`` (WTK-005), which runs
  the dirty guard and broadcasts the total, cross-window end. The header
  contributes only :func:`request_logout` — the seam that turns the guard's
  refusal into the educate-voice confirmation prompt instead of an
  exception reaching the shell.
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from mentorapp.access.grants import GrantLookup, roles_cover_data_source
from mentorapp.observability import get_logger
from mentorapp.ui.auth_flows import (
    EducateMessage,
    UnsavedWorkGuardError,
    WindowSessionController,
)
from mentorapp.ui.home_panel import ACCOUNT_MENU, HOME_FRAME, MenuItem
from mentorapp.ui.navigation import Panel, ViewRecord

log = get_logger(__name__)

QUICK_OPEN_SHORTCUT = "Ctrl+K"


# --- The header, for every window ----------------------------------------------


@dataclass(frozen=True)
class StandardHeader:
    """The one thin header (REQ-009): zones as stable keys, not pixels.

    ``left`` always starts with identity; ``navigation`` follows only when
    the window hosts panels. ``right`` and ``account_menu`` are the WTK-019
    rulings carried by reference — a header can never drift from the home
    frame's definition. ``quick_open_shortcut`` is declared here so the
    shell binds Ctrl+K identically on every window kind.
    """

    has_navigation: bool
    right: tuple[str, ...] = HOME_FRAME.header_right
    account_menu: tuple[MenuItem, ...] = field(default=ACCOUNT_MENU)
    quick_open_shortcut: str = QUICK_OPEN_SHORTCUT

    @property
    def left(self) -> tuple[str, ...]:
        """The left zones, derived — omitting navigation can't desync a flag."""
        if self.has_navigation:
            return ("identity", "navigation")
        return ("identity",)


MAIN_WINDOW_HEADER = StandardHeader(has_navigation=True)

# Pop-outs are record windows, not panel hosts (layout standard; WTK-021's
# POP_OUT_HAS_NAVIGATION pins the same fact on the preview side).
POP_OUT_HEADER = StandardHeader(has_navigation=False)


def header_for_window(*, is_pop_out: bool) -> StandardHeader:
    """The header a window renders; the pop-out distinction is the only fork."""
    return POP_OUT_HEADER if is_pop_out else MAIN_WINDOW_HEADER


# --- The quick-open palette (Ctrl+K) --------------------------------------------


class QuickOpenKind(enum.StrEnum):
    PANEL = "panel"
    VIEW = "view"


@dataclass(frozen=True)
class QuickOpenEntry:
    """One palette destination: opening it is a panel with an optional view.

    ``view_key`` of ``None`` means "the panel itself" — the shell opens it
    on the user's last-displayed view, exactly as ordinary navigation would.
    """

    kind: QuickOpenKind
    label: str
    panel_key: str
    view_key: str | None = None


def quick_open_entries(
    panels: Iterable[Panel],
    views: Iterable[ViewRecord],
    *,
    grants: GrantLookup,
    user_id: uuid.UUID,
    user_roles: frozenset[str],
) -> tuple[QuickOpenEntry, ...]:
    """Every destination the user can reach (REQ-009), permissioned once.

    Panel permission IS data-source permission — the quiet
    :func:`roles_cover_data_source` form, because listing what to show is
    not an open attempt (grants.py draws that line). A view is reachable
    exactly when it is alive and its panel is; a view whose panel is gone
    from the catalog is unreachable, not an error. Panels come first, then
    views, each alphabetical — a stable order for the empty-query palette.
    """
    reachable: dict[str, Panel] = {}
    for panel in panels:
        if panel.data_source_key is None or roles_cover_data_source(
            grants, data_source_key=panel.data_source_key, user_roles=user_roles
        ):
            reachable[panel.panel_key] = panel
    entries = [
        QuickOpenEntry(QuickOpenKind.PANEL, panel.title, panel.panel_key)
        for panel in reachable.values()
    ]
    entries.sort(key=lambda e: e.label.casefold())
    view_entries = [
        QuickOpenEntry(QuickOpenKind.VIEW, view.name, view.panel_key, view.view_key)
        for view in views
        if view.deleted_at is None and view.panel_key in reachable
    ]
    view_entries.sort(key=lambda e: e.label.casefold())
    entries.extend(view_entries)
    log.info(
        "quick-open palette composed",
        extra={"context": {"userId": str(user_id), "entryCount": len(entries)}},
    )
    return tuple(entries)


def search_quick_open(
    entries: Iterable[QuickOpenEntry], query: str
) -> tuple[QuickOpenEntry, ...]:
    """Type-ahead over the palette: substring match, prefix matches first.

    Case-insensitive on the label; a blank query returns everything in the
    given order (the palette opens as a full catalog before the first
    keystroke). Within each band the incoming order — alphabetical from
    :func:`quick_open_entries` — is kept, so ranking is deterministic.
    """
    needle = query.strip().casefold()
    if not needle:
        return tuple(entries)
    prefix: list[QuickOpenEntry] = []
    contains: list[QuickOpenEntry] = []
    for entry in entries:
        label = entry.label.casefold()
        if label.startswith(needle):
            prefix.append(entry)
        elif needle in label:
            contains.append(entry)
    return tuple(prefix + contains)


# --- Logout from the user menu ---------------------------------------------------


LOGOUT_UNSAVED_WORK = EducateMessage(
    what_happened="You are still signed in — logging out was paused.",
    why=(
        "Logging out ends every open window of this session at once, and "
        "some of your windows hold unsaved changes that would be discarded."
    ),
    what_next=("Save your work first, or confirm the discard to log out everywhere anyway."),
)


def request_logout(
    controller: WindowSessionController, *, discard_confirmed: bool = False
) -> EducateMessage | None:
    """The user-menu Log out item: delegate to the session controller (WTK-005).

    Returns ``None`` when the session ended (totally, across windows), or
    :data:`LOGOUT_UNSAVED_WORK` when the dirty guard refused — the shell
    re-invokes with ``discard_confirmed=True`` only after the user's
    explicit confirmation. The guard itself lives in the controller; this
    seam only translates its refusal into the educate voice.
    """
    try:
        controller.logout(discard_confirmed=discard_confirmed)
    except UnsavedWorkGuardError:
        return LOGOUT_UNSAVED_WORK
    log.info("logout invoked from the header user menu")
    return None
