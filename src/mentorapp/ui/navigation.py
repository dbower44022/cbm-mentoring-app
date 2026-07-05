"""Navigation design: presentations, pins, views, broken-pin fallback (WTK-020).

The UI-layer design for REQ-010 (panel navigation and pinning) and REQ-015
(broken pin and startup fallback). No frontend shell exists yet (PI-002), so
the design is executable surface the shell renders verbatim:

- **The pin set is the data; presentation is only rendering.** A
  :class:`NavigationProfile` holds one ordered pin set plus one
  :class:`NavigationPresentation` (tabs / side menu / group tree).
  :func:`switch_navigation_presentation` changes ONLY the presentation —
  the pin tuple is carried through untouched, so switching can never lose,
  reorder, or re-derive pins.
- **A pin is a panel opened with a view active.** :class:`Pin` references a
  panel key and a view key, never a copy of either — the pinned entry always
  opens whatever the view currently is.
- **One preference document, one persistence seam.** The whole profile
  round-trips as the ``navigation`` preference document
  (:func:`navigation_preference_document` /
  :func:`navigation_profile_from_document`) through the REQ-060 preference
  pair — no new table, column, or endpoint. Parsing is tolerant: an unknown
  presentation or malformed pin entry degrades to a default, never a blank
  navigation.
- **Panel permission IS data-source permission.** :func:`resolve_pin` asks
  :func:`mentorapp.access.grants.authorize_data_source` — the one access
  boundary — and invents no second permission model. A panel without a data
  source (Home) is open to every signed-in user.
- **Broken pins are marked, explained, never silently removed.**
  :func:`resolve_pin` returns every pin, tagging unavailable ones with a
  :class:`PinBreak`; :func:`broken_pin_fallback` turns the break into the
  educate-voice explanation (what happened — by whom and when, where the
  soft-delete tombstone carries it — and what next) with the two standard
  choices: remove the pin or choose a different view. Deep links and the
  open-to-last-panel startup ride the same path via
  :func:`resolve_startup_target`, landing on Home with the explanation
  instead of a blank screen.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from mentorapp.access.grants import (
    DataSourceAccessError,
    GrantLookup,
    authorize_data_source,
)
from mentorapp.observability import get_logger
from mentorapp.ui.auth_flows import EducateMessage

log = get_logger(__name__)


# --- The navigation profile ----------------------------------------------------


class NavigationPresentation(enum.StrEnum):
    """The three switchable renderings of one and the same pin set (REQ-010)."""

    TABS = "tabs"
    SIDE_MENU = "sideMenu"
    GROUP_TREE = "groupTree"


DEFAULT_PRESENTATION = NavigationPresentation.TABS

# The whole profile is ONE document under the REQ-060 preference pair: switching
# presentation and editing pins are saves of the same key, so the two can never
# drift apart across windows or sessions.
NAVIGATION_PREFERENCE_KEY = "navigation"


@dataclass(frozen=True)
class Pin:
    """One personal navigation entry: a panel opened with a view active.

    ``group`` exists for the group-tree presentation; the flat presentations
    render grouped pins in pin order and ignore the grouping — a pin never
    changes identity when the presentation changes.
    """

    pin_key: str
    panel_key: str
    view_key: str
    label: str
    group: str | None = None


@dataclass(frozen=True)
class NavigationProfile:
    """A user's navigation: the pin set (the data) plus its rendering."""

    presentation: NavigationPresentation = DEFAULT_PRESENTATION
    pins: tuple[Pin, ...] = ()


def switch_navigation_presentation(
    profile: NavigationProfile, presentation: NavigationPresentation
) -> NavigationProfile:
    """Re-render navigation in another style; the pin set is untouchable here.

    Returns a profile carrying the SAME pin tuple — this function has no way
    to add, drop, or reorder pins, which is how REQ-010's "switchable anytime
    without losing pins" is guaranteed rather than merely intended.
    """
    log.info(
        "navigation presentation switched",
        extra={
            "context": {
                "fromPresentation": profile.presentation,
                "toPresentation": presentation,
                "pinCount": len(profile.pins),
            }
        },
    )
    return NavigationProfile(presentation=presentation, pins=profile.pins)


def navigation_preference_document(profile: NavigationProfile) -> dict[str, Any]:
    """Serialize the profile as the ``navigation`` preference document."""
    return {
        "presentation": profile.presentation.value,
        "pins": [
            {
                "pinKey": pin.pin_key,
                "panelKey": pin.panel_key,
                "viewKey": pin.view_key,
                "label": pin.label,
                "group": pin.group,
            }
            for pin in profile.pins
        ],
    }


def navigation_profile_from_document(document: dict[str, Any]) -> NavigationProfile:
    """Parse the stored document, degrading — never failing — on bad content.

    The document is opaque client state written by older and newer shells
    alike, so unknown presentations fall back to the default and malformed
    pin entries are skipped with a log line; a stale document must never
    leave the user with no navigation at all.
    """
    raw_presentation = document.get("presentation")
    try:
        presentation = NavigationPresentation(raw_presentation)
    except ValueError:
        log.warning(
            "unknown navigation presentation; using default",
            extra={"context": {"presentation": raw_presentation}},
        )
        presentation = DEFAULT_PRESENTATION
    pins: list[Pin] = []
    raw_pins = document.get("pins")
    for entry in raw_pins if isinstance(raw_pins, list) else []:
        try:
            pins.append(
                Pin(
                    pin_key=str(entry["pinKey"]),
                    panel_key=str(entry["panelKey"]),
                    view_key=str(entry["viewKey"]),
                    label=str(entry["label"]),
                    group=None if entry.get("group") is None else str(entry["group"]),
                )
            )
        except (TypeError, KeyError):
            log.warning(
                "malformed pin entry skipped", extra={"context": {"entry": repr(entry)}}
            )
    return NavigationProfile(presentation=presentation, pins=tuple(pins))


# --- Panels and views -----------------------------------------------------------


class PanelType(enum.StrEnum):
    """Panel kinds the shell can host; later types (Gantt, graph) extend this."""

    GRID = "grid"
    DASHBOARD = "dashboard"


HOME_PANEL_KEY = "home"


@dataclass(frozen=True)
class Panel:
    """A hostable surface. Permission derives from its data source, nothing else.

    ``data_source_key`` of ``None`` means the panel carries no data source of
    its own (Home) and is open to every signed-in user — there is no separate
    per-panel grant model to fall out of sync with the REQ-006 boundary.
    """

    panel_key: str
    title: str
    panel_type: PanelType
    data_source_key: str | None = None


HOME_PANEL = Panel(panel_key=HOME_PANEL_KEY, title="Home", panel_type=PanelType.DASHBOARD)


@dataclass(frozen=True)
class ViewRecord:
    """A view as navigation sees it: which panel it opens, plus its tombstone.

    Soft deletes are system-wide, so a removed view is a live row with
    ``deleted_at``/``deleted_by`` set — exactly the "what, by whom, when" the
    broken-pin explanation must name (REQ-015). ``None`` in both means the
    view is alive.
    """

    view_key: str
    name: str
    panel_key: str
    deleted_at: datetime | None = None
    deleted_by: str | None = None


class NavigationCatalog(Protocol):
    """The lookup seam for panels and views (persisted by the storage area)."""

    def panel(self, panel_key: str) -> Panel | None:
        """Return the panel, or ``None`` when no such panel exists."""
        ...

    def view(self, view_key: str) -> ViewRecord | None:
        """Return the view INCLUDING soft-deleted tombstones; ``None`` if unknown."""
        ...


@dataclass
class InMemoryNavigationCatalog:
    """Reference :class:`NavigationCatalog` for tests and pre-persistence wiring."""

    panels: dict[str, Panel] = field(default_factory=dict)
    views: dict[str, ViewRecord] = field(default_factory=dict)

    def panel(self, panel_key: str) -> Panel | None:
        return self.panels.get(panel_key)

    def view(self, view_key: str) -> ViewRecord | None:
        return self.views.get(view_key)


# --- Broken pins: marked and explained, never removed ---------------------------


class PinBreakReason(enum.StrEnum):
    VIEW_REMOVED = "viewRemoved"
    PANEL_REMOVED = "panelRemoved"
    ACCESS_REVOKED = "accessRevoked"


class BrokenPinChoice(enum.StrEnum):
    """The two ways OUT of a broken pin — both explicit user acts."""

    REMOVE_PIN = "removePin"
    CHOOSE_DIFFERENT_VIEW = "chooseDifferentView"


@dataclass(frozen=True)
class PinBreak:
    """Why a pin can't open, with the tombstone facts when the store has them."""

    reason: PinBreakReason
    subject: str
    actor: str | None = None
    when: datetime | None = None


@dataclass(frozen=True)
class ResolvedPin:
    """A pin as the shell renders it: always present, marked when broken."""

    pin: Pin
    break_: PinBreak | None = None

    @property
    def is_broken(self) -> bool:
        return self.break_ is not None


def resolve_pin(
    pin: Pin,
    *,
    catalog: NavigationCatalog,
    grants: GrantLookup,
    user_id: uuid.UUID,
    user_roles: frozenset[str],
) -> ResolvedPin:
    """Health-check one pin against the catalog and the REQ-006 grant boundary.

    Always returns the pin — a broken one comes back marked with its
    :class:`PinBreak`, never dropped (REQ-015). Checks in the order a user
    would hit the failures: the view, then its panel, then access.
    """
    view = catalog.view(pin.view_key)
    if view is None or view.deleted_at is not None:
        subject = pin.label if view is None else view.name
        actor = None if view is None else view.deleted_by
        when = None if view is None else view.deleted_at
        return ResolvedPin(
            pin, PinBreak(PinBreakReason.VIEW_REMOVED, subject, actor=actor, when=when)
        )
    panel = catalog.panel(view.panel_key)
    if panel is None:
        return ResolvedPin(pin, PinBreak(PinBreakReason.PANEL_REMOVED, view.panel_key))
    if panel.data_source_key is not None:
        try:
            authorize_data_source(
                grants,
                data_source_key=panel.data_source_key,
                user_id=user_id,
                user_roles=user_roles,
            )
        except DataSourceAccessError:
            return ResolvedPin(
                pin, PinBreak(PinBreakReason.ACCESS_REVOKED, panel.data_source_key)
            )
    return ResolvedPin(pin)


def _removal_clause(break_: PinBreak) -> str:
    # "by whom, when" renders only when the tombstone carries it — the message
    # never fabricates an actor or a time it does not have.
    clause = ""
    if break_.actor is not None:
        clause += f" by {break_.actor}"
    if break_.when is not None:
        clause += f" on {break_.when.date().isoformat()}"
    return clause


def broken_pin_fallback(pin: Pin, break_: PinBreak) -> EducateMessage:
    """BrokenPinFallback: the educate-voice explanation for one broken pin.

    What happened → why (naming what was deleted or revoked, by whom and when
    where known) → what next, always offering :class:`BrokenPinChoice` — the
    pin stays until the USER removes or repoints it.
    """
    what_next = (
        "Remove this pin, or choose a different view to pin here — "
        "the pin stays until you decide."
    )
    if break_.reason is PinBreakReason.VIEW_REMOVED:
        why = f"The view '{break_.subject}' was removed{_removal_clause(break_)}."
        what_next = (
            "An administrator can restore the view — nothing is ever "
            "physically deleted. Otherwise remove this pin or choose a "
            "different view to pin here."
        )
    elif break_.reason is PinBreakReason.PANEL_REMOVED:
        why = f"The panel '{break_.subject}' no longer exists{_removal_clause(break_)}."
    else:
        why = (
            f"Opening it needs access to the data source '{break_.subject}', "
            "which your roles are not granted — an administrator grants "
            "data-source access."
        )
    return EducateMessage(
        what_happened=f"The pin '{pin.label}' can't open right now.",
        why=why,
        what_next=what_next,
    )


# --- Startup and deep links: the same fallback, never a blank screen ------------


@dataclass(frozen=True)
class StartupResolution:
    """Where the window actually opens; ``notice`` is set when it isn't the target."""

    panel_key: str
    view_key: str | None
    notice: EducateMessage | None = None


def resolve_startup_target(
    pin: Pin,
    *,
    catalog: NavigationCatalog,
    grants: GrantLookup,
    user_id: uuid.UUID,
    user_roles: frozenset[str],
) -> StartupResolution:
    """Resolve open-to-last-panel or a deep link through the broken-pin rules.

    A healthy target opens as asked; an unavailable one lands on Home
    carrying the same explanation a broken pin shows (REQ-015) — never a
    blank screen, and never a silent redirect.
    """
    resolved = resolve_pin(
        pin, catalog=catalog, grants=grants, user_id=user_id, user_roles=user_roles
    )
    if resolved.break_ is None:
        return StartupResolution(panel_key=pin.panel_key, view_key=pin.view_key)
    log.info(
        "startup target unavailable; falling back to Home",
        extra={
            "context": {
                "panelKey": pin.panel_key,
                "viewKey": pin.view_key,
                "reason": resolved.break_.reason,
            }
        },
    )
    return StartupResolution(
        panel_key=HOME_PANEL_KEY,
        view_key=None,
        notice=broken_pin_fallback(pin, resolved.break_),
    )
