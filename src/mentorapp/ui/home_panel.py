"""Home panel & admin messaging design: frame, startup, dashlets, messages (WTK-019).

The UI-layer design for REQ-003 (mentor home screen) and REQ-011 (Home panel
and admin messaging). No frontend shell exists yet (PI-002), so — like
``auth_flows`` — the design is executable surface the shell renders verbatim:

- **The home frame is declared, not hand-built.** :data:`HOME_FRAME` fixes the
  REQ-003 geometry (CBM logo upper-left, avatar + name upper-right opening
  :data:`ACCOUNT_MENU`, the user's permissioned Areas down the left edge) and
  the layout standard's header right side (notification bell, Help, account
  menu). The Areas rail is optional by data, not by mode: it simply has no
  entries for a Home-only user.
- **Startup is honored, never a blank screen.** :func:`resolve_startup_panel`
  applies the per-user preference (open to Home, or to the last panel; system
  default Home). A last panel that no longer exists or is no longer permitted
  lands on Home WITH an educate-voice notice — the layout standard's
  broken-pin fallback rule.
- **Home = admin messaging + the user's chosen dashlets.** The message-list
  dashlet is provided, always first, and not user-removable; chosen dashlets
  whose view or permission went away stay visible with an explanation —
  never silently removed. Both the startup choice and the dashlet layout
  persist through the one preference mechanism (``GET/PUT /preferences``,
  REQ-060) under :data:`STARTUP_PREFERENCE_KEY` / :data:`HOME_DASHLETS_PREFERENCE_KEY`
  — a new table or endpoint for either would be a defect.
- **Messages carry per-user state.** :class:`MessageCenter` is the reference
  behavior the storage area later backs with a table: read is automatic on
  view (opening Home reads what it displays), acknowledgment is only ever an
  explicit click the admin can audit, and an urgent message banners across
  every panel until READ — acknowledgment tracking never extends the banner,
  because the banner exists to guarantee delivery, not consent.
"""

from __future__ import annotations

import enum
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from mentorapp.observability import get_logger
from mentorapp.ui.auth_flows import EducateMessage

log = get_logger(__name__)

HOME_PANEL_KEY = "home"

# Both ride the userPreference mechanism (REQ-060): org-default rows give the
# system defaults, the user's own row overrides. Never a dedicated table.
STARTUP_PREFERENCE_KEY = "shell.startup"
HOME_DASHLETS_PREFERENCE_KEY = "home.dashlets"


# --- The home frame (REQ-003) --------------------------------------------------


@dataclass(frozen=True)
class MenuItem:
    key: str
    label: str


# The avatar dropdown is the address for all per-user preferences (layout
# standard). Help is last because the last item in EVERY menu is Help — the
# app-wide rule the tests pin.
ACCOUNT_MENU: tuple[MenuItem, ...] = (
    MenuItem("navigationStyle", "Navigation style"),
    MenuItem("startup", "Startup"),
    MenuItem("themes", "Themes"),
    MenuItem("manageViewsAndPins", "Manage views & pins"),
    MenuItem("logout", "Log out"),
    MenuItem("help", "Help"),
)


@dataclass(frozen=True)
class HomeFrame:
    """The REQ-003 screen frame the shell renders verbatim.

    Zones are stable keys, not pixels: the CBM logo upper-left (links Home),
    the user's avatar + full name upper-right opening :data:`ACCOUNT_MENU`,
    and the permissioned Areas down the left edge. ``header_right`` is the
    layout standard's fixed right side; pop-out windows drop the Areas rail
    (they are record windows, not panel hosts) but keep this header.
    """

    logo_zone: str = "upperLeft"
    identity_zone: str = "upperRight"
    areas_zone: str = "leftEdge"
    header_right: tuple[str, ...] = ("notificationBell", "help", "accountMenu")
    account_menu: tuple[MenuItem, ...] = ACCOUNT_MENU

    def areas_for(self, accessible_panel_keys: Sequence[str]) -> tuple[str, ...]:
        """The Areas rail: every permissioned panel except Home itself.

        Order is the caller's (system default first, then pins). May be
        empty — the rail renders nothing rather than an empty chrome box
        (white space is a waste).
        """
        return tuple(k for k in accessible_panel_keys if k != HOME_PANEL_KEY)


HOME_FRAME = HomeFrame()


# --- Startup preference (REQ-011: HonorStartupPreference) ----------------------


class StartupChoice(enum.StrEnum):
    HOME = "home"
    LAST_PANEL = "lastPanel"


# System default: Home. Seeded as the STARTUP_PREFERENCE_KEY org-default row.
DEFAULT_STARTUP_CHOICE = StartupChoice.HOME

STARTUP_FALLBACK_TO_HOME = EducateMessage(
    what_happened="Your last panel couldn't be opened.",
    why="The view behind it was changed or removed, or your access to it changed.",
    what_next=(
        "You've landed on Home instead. Open the panel from your navigation to "
        "see exactly what changed, or pick a different startup preference from "
        "your account menu."
    ),
)


@dataclass(frozen=True)
class StartupTarget:
    """Where login lands; ``notice`` is set only when the choice couldn't be honored."""

    panel_key: str
    notice: EducateMessage | None = None


def resolve_startup_panel(
    choice: StartupChoice,
    last_panel_key: str | None,
    accessible_panel_keys: Collection[str],
) -> StartupTarget:
    """Apply the per-user startup preference; never land on a blank screen.

    Home is always accessible, so :data:`StartupChoice.HOME` resolves
    unconditionally. ``LAST_PANEL`` with no recorded last panel (first login)
    is Home WITHOUT a notice — nothing went wrong. A recorded last panel that
    is gone or unpermitted is Home WITH :data:`STARTUP_FALLBACK_TO_HOME`
    (broken-pin fallback rule: explain, never silently redirect).
    """
    if choice is StartupChoice.LAST_PANEL and last_panel_key is not None:
        if last_panel_key == HOME_PANEL_KEY or last_panel_key in accessible_panel_keys:
            return StartupTarget(last_panel_key)
        log.info(
            "startup fallback to home: last panel not openable",
            extra={"context": {"lastPanelKey": last_panel_key}},
        )
        return StartupTarget(HOME_PANEL_KEY, notice=STARTUP_FALLBACK_TO_HOME)
    return StartupTarget(HOME_PANEL_KEY)


# --- Dashlets (REQ-011) ---------------------------------------------------------


@dataclass(frozen=True)
class DashletRef:
    """A user's chosen dashlet: a view rendered small (layout standard)."""

    view_key: str
    title: str


@dataclass(frozen=True)
class ResolvedDashlet:
    """A dashlet as Home renders it; ``notice`` set = broken but still shown."""

    view_key: str
    title: str
    notice: EducateMessage | None = None


# Provided, always first, not user-removable: Home IS admin messaging plus
# chosen dashlets, so an empty dashlet choice can never hide admin messages.
MESSAGES_DASHLET = ResolvedDashlet(
    view_key="home.messages",
    title="Messages from your administrator",
)


def dashlet_unavailable_message(title: str) -> EducateMessage:
    """Educate-voice notice for a chosen dashlet whose view went away."""
    return EducateMessage(
        what_happened=f"The dashlet '{title}' can't be shown.",
        why="The view behind it was removed, or your access to its data source changed.",
        what_next=(
            "Remove it from your Home or choose a different view as a dashlet — "
            "your other dashlets are unaffected."
        ),
    )


def resolve_home_dashlets(
    chosen: Sequence[DashletRef],
    available_view_keys: Collection[str],
) -> tuple[ResolvedDashlet, ...]:
    """Compose Home's dashlet list in the user's saved order.

    The messages dashlet leads unconditionally. A chosen dashlet whose view is
    no longer available stays VISIBLE with an educate-voice notice — the
    broken-pin rule applied to dashlets; silently dropping it would lose the
    user's arrangement without explanation.
    """
    resolved = [MESSAGES_DASHLET]
    for ref in chosen:
        if ref.view_key in available_view_keys:
            resolved.append(ResolvedDashlet(ref.view_key, ref.title))
        else:
            resolved.append(
                ResolvedDashlet(ref.view_key, ref.title, dashlet_unavailable_message(ref.title))
            )
    return tuple(resolved)


# --- Admin messaging (REQ-011) --------------------------------------------------


class MessagePriority(enum.StrEnum):
    NORMAL = "normal"  # shown on Home only
    URGENT = "urgent"  # banners across every panel until read


@dataclass(frozen=True)
class AdminMessage:
    """One admin-posted message; expiration is admin-set and optional."""

    key: str
    title: str
    body: str
    posted_by: str
    posted_at: datetime
    expires_at: datetime | None = None
    priority: MessagePriority = MessagePriority.NORMAL
    requires_acknowledgment: bool = False

    def visible_at(self, now: datetime) -> bool:
        return self.expires_at is None or now < self.expires_at


class UnknownMessageError(Exception):
    """A message key the center has never seen."""


class AcknowledgmentNotRequestedError(Exception):
    """Acknowledge invoked on a message that never asked for acknowledgment."""


@dataclass
class _MessageState:
    message: AdminMessage
    read_by: set[str] = field(default_factory=set)
    acknowledged_by: set[str] = field(default_factory=set)


class MessageCenter:
    """Reference per-user message state; the storage area backs it with a table.

    Owns the three REQ-011 invariants: read is automatic on view (displaying
    a message IS reading it — no separate mark-as-read chore), acknowledgment
    only ever happens by explicit click and is auditable by the admin, and
    the urgent banner clears on READ, not on acknowledgment — the banner
    guarantees delivery; the acknowledgment report covers consent.
    """

    def __init__(self) -> None:
        self._states: dict[str, _MessageState] = {}

    def post(self, message: AdminMessage) -> None:
        """Publish (or re-publish, replacing) an admin message to every user."""
        self._states[message.key] = _MessageState(message)
        log.info(
            "admin message posted",
            extra={"context": {"messageKey": message.key, "priority": message.priority}},
        )

    def visible_messages(self, now: datetime) -> tuple[AdminMessage, ...]:
        """Every unexpired message, newest first (the message dashlet's order)."""
        live = (s.message for s in self._states.values() if s.message.visible_at(now))
        return tuple(sorted(live, key=lambda m: m.posted_at, reverse=True))

    def unread_count(self, user_id: str, now: datetime) -> int:
        """The Home badge: unexpired messages this user has not yet seen."""
        return sum(
            1
            for s in self._states.values()
            if s.message.visible_at(now) and user_id not in s.read_by
        )

    def view_home(self, user_id: str, now: datetime) -> tuple[AdminMessage, ...]:
        """Render the message dashlet for one user: returns AND reads its messages.

        Auto-read on view — what the dashlet displayed, the user has read.
        Expired messages neither render nor read.
        """
        messages = self.visible_messages(now)
        for message in messages:
            self._states[message.key].read_by.add(user_id)
        return messages

    def urgent_banner(self, user_id: str, now: datetime) -> tuple[AdminMessage, ...]:
        """Unexpired urgent messages this user has NOT read: every panel banners
        these until a view reads them (:meth:`view_home`, or the banner's own
        open-the-message act)."""
        return tuple(
            m
            for m in self.visible_messages(now)
            if m.priority is MessagePriority.URGENT
            and user_id not in self._states[m.key].read_by
        )

    def acknowledge(self, user_id: str, message_key: str) -> None:
        """Record one user's EXPLICIT acknowledgment click.

        Never implicit: no view, banner, or read ever acknowledges. Guards
        make misuse loud — an unknown key and a message that never asked for
        acknowledgment are both caller bugs, not user states. Acknowledging
        implies having read (the click sits on the rendered message).
        """
        state = self._states.get(message_key)
        if state is None:
            raise UnknownMessageError(message_key)
        if not state.message.requires_acknowledgment:
            raise AcknowledgmentNotRequestedError(message_key)
        state.read_by.add(user_id)
        state.acknowledged_by.add(user_id)
        log.info(
            "admin message acknowledged",
            extra={"context": {"messageKey": message_key, "userId": user_id}},
        )

    def outstanding_acknowledgments(
        self, message_key: str, user_ids: Collection[str]
    ) -> tuple[str, ...]:
        """The admin's audit: who, of ``user_ids``, has not acknowledged yet.

        Expiration does not close the books — an expired message leaves Home,
        but the admin can still see who never acknowledged it.
        """
        state = self._states.get(message_key)
        if state is None:
            raise UnknownMessageError(message_key)
        if not state.message.requires_acknowledgment:
            raise AcknowledgmentNotRequestedError(message_key)
        return tuple(u for u in user_ids if u not in state.acknowledged_by)
