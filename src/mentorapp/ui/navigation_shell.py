"""Navigation as the shell runs it: rendering, opening pins, the dialog (WTK-028).

The build over the WTK-020 navigation design for REQ-010 (switchable
presentations preserving pins; a pin opens its panel with its view active)
and REQ-015 (the broken-pin explanation dialog offering remove or reselect).
No frontend shell exists yet (PI-002), so — like its siblings — this is
executable surface the shell renders verbatim:

- **One pin set, three renderings.** :func:`render_navigation` turns the
  profile's resolved pins into the structure each presentation displays.
  Tabs and the side menu are flat: pin order, grouping ignored; the group
  tree buckets pins under their ``group`` in order of first appearance.
  Every rendering is derived from the SAME resolved tuple, so switching
  (``switch_navigation_presentation``) can only ever change the shape,
  never the membership — REQ-010's guarantee, carried into rendering.
- **Opening a pin is resolving it.** :func:`open_pin` returns a
  :class:`PanelOpening` (the panel with the referenced view active) for a
  healthy pin, and the :class:`BrokenPinDialog` for a broken one — a broken
  pin is always *clickable*; the click produces the explanation, never a
  dead control (educate, never hide).
- **The dialog's choices are the only exits.** :class:`BrokenPinDialog`
  binds the WTK-020 educate message to the two :class:`BrokenPinChoice`
  options; :func:`remove_pin` and :func:`repoint_pin` apply them, each
  returning a new profile the shell persists through the one preference
  seam (``navigation_preference_document``). Nothing else ever drops a pin.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from mentorapp.access.grants import GrantLookup
from mentorapp.observability import get_logger
from mentorapp.ui.auth_flows import EducateMessage
from mentorapp.ui.navigation import (
    BrokenPinChoice,
    NavigationCatalog,
    NavigationPresentation,
    NavigationProfile,
    Pin,
    PinBreak,
    ResolvedPin,
    ViewRecord,
    broken_pin_fallback,
    resolve_pin,
)

log = get_logger(__name__)


# --- Resolving the whole profile --------------------------------------------------


def resolve_navigation(
    profile: NavigationProfile,
    *,
    catalog: NavigationCatalog,
    grants: GrantLookup,
    user_id: uuid.UUID,
    user_roles: frozenset[str],
) -> tuple[ResolvedPin, ...]:
    """Health-check every pin in profile order, keeping broken ones marked.

    The tuple always has exactly one entry per pin (REQ-015: broken pins are
    never silently removed), in the profile's order — position is user data.
    """
    return tuple(
        resolve_pin(pin, catalog=catalog, grants=grants, user_id=user_id, user_roles=user_roles)
        for pin in profile.pins
    )


# --- Rendering: three shapes of the one pin set (REQ-010) --------------------------


@dataclass(frozen=True)
class NavigationItem:
    """One rendered entry: what any presentation shows for one resolved pin.

    ``is_broken`` drives the subtle broken marking; activating a broken item
    opens the dialog (:func:`open_pin`), a healthy one its panel and view.
    """

    pin_key: str
    label: str
    panel_key: str
    view_key: str
    is_broken: bool


@dataclass(frozen=True)
class NavigationGroup:
    """A bucket of items. ``label`` is ``None`` for the ungrouped bucket."""

    label: str | None
    items: tuple[NavigationItem, ...]


@dataclass(frozen=True)
class NavigationRendering:
    """What the shell draws: groups of items shaped for one presentation.

    Flat presentations carry exactly one anonymous group; the group tree may
    carry several. The shell renders groups and items verbatim, in order.
    """

    presentation: NavigationPresentation
    groups: tuple[NavigationGroup, ...]


def _item(resolved: ResolvedPin) -> NavigationItem:
    pin = resolved.pin
    return NavigationItem(
        pin_key=pin.pin_key,
        label=pin.label,
        panel_key=pin.panel_key,
        view_key=pin.view_key,
        is_broken=resolved.is_broken,
    )


def render_navigation(
    presentation: NavigationPresentation, resolved: tuple[ResolvedPin, ...]
) -> NavigationRendering:
    """Shape the resolved pin set for one presentation (REQ-010).

    Tabs and the side menu are the same flat rendering — one anonymous group
    in pin order, grouping ignored (a pin never changes identity when the
    presentation changes; WTK-020). The group tree buckets pins by ``group``
    in order of first appearance, ungrouped pins under the anonymous bucket
    positioned where its first member appears. Every presentation renders
    every pin, broken ones included.
    """
    if presentation is not NavigationPresentation.GROUP_TREE:
        return NavigationRendering(
            presentation=presentation,
            groups=(NavigationGroup(label=None, items=tuple(map(_item, resolved))),),
        )
    buckets: dict[str | None, list[NavigationItem]] = {}
    for entry in resolved:
        buckets.setdefault(entry.pin.group, []).append(_item(entry))
    return NavigationRendering(
        presentation=presentation,
        groups=tuple(
            NavigationGroup(label=label, items=tuple(items)) for label, items in buckets.items()
        ),
    )


# --- Opening a pin (REQ-010) and the broken-pin dialog (REQ-015) -------------------


@dataclass(frozen=True)
class PanelOpening:
    """A healthy activation: open this panel with this view active."""

    panel_key: str
    view_key: str


# The dialog offers exactly the two WTK-020 exits, remove first — matching the
# fallback message's wording order ("Remove this pin, or choose a different
# view"). There is no third, silent exit.
BROKEN_PIN_CHOICES = (BrokenPinChoice.REMOVE_PIN, BrokenPinChoice.CHOOSE_DIFFERENT_VIEW)


@dataclass(frozen=True)
class BrokenPinDialog:
    """The explanation a broken pin opens: the educate message plus the exits.

    The shell maps each choice to its apply function — ``removePin`` to
    :func:`remove_pin`, ``chooseDifferentView`` to :func:`repoint_pin` after
    the user picks a replacement (the standard lookup control over views).
    Dismissing the dialog changes nothing: the pin stays, still marked.
    """

    pin: Pin
    break_: PinBreak
    message: EducateMessage
    choices: tuple[BrokenPinChoice, ...] = BROKEN_PIN_CHOICES


def open_pin(
    pin: Pin,
    *,
    catalog: NavigationCatalog,
    grants: GrantLookup,
    user_id: uuid.UUID,
    user_roles: frozenset[str],
) -> PanelOpening | BrokenPinDialog:
    """Activate a pin: its panel with its view active, or the dialog (REQ-015).

    Resolution happens at activation time, not render time — a pin that broke
    after the navigation was drawn still explains itself instead of opening a
    dead panel, and one repaired since simply opens.
    """
    resolved = resolve_pin(
        pin, catalog=catalog, grants=grants, user_id=user_id, user_roles=user_roles
    )
    if resolved.break_ is None:
        return PanelOpening(panel_key=pin.panel_key, view_key=pin.view_key)
    log.info(
        "broken pin activated; explanation dialog opened",
        extra={
            "context": {
                "userId": str(user_id),
                "pinKey": pin.pin_key,
                "reason": resolved.break_.reason,
            }
        },
    )
    return BrokenPinDialog(
        pin=pin, break_=resolved.break_, message=broken_pin_fallback(pin, resolved.break_)
    )


# --- Applying the dialog's choices --------------------------------------------------


class UnknownPinError(LookupError):
    """The pin_key is not in the profile — usually another window removed it."""

    def __init__(self, pin_key: str) -> None:
        super().__init__(f"no pin '{pin_key}' in the navigation profile")
        self.pin_key = pin_key


def _require_pin(profile: NavigationProfile, pin_key: str) -> Pin:
    for pin in profile.pins:
        if pin.pin_key == pin_key:
            return pin
    raise UnknownPinError(pin_key)


def remove_pin(profile: NavigationProfile, pin_key: str) -> NavigationProfile:
    """Apply ``removePin``: the ONE way a pin leaves the set — an explicit act.

    Returns the profile without that pin (order otherwise untouched); the
    shell persists it through the navigation preference document. Raises
    :class:`UnknownPinError` when the pin is already gone, so a cross-window
    race surfaces instead of silently succeeding twice.
    """
    removed = _require_pin(profile, pin_key)
    log.info(
        "pin removed",
        extra={"context": {"pinKey": pin_key, "panelKey": removed.panel_key}},
    )
    return NavigationProfile(
        presentation=profile.presentation,
        pins=tuple(pin for pin in profile.pins if pin.pin_key != pin_key),
    )


def repoint_pin(
    profile: NavigationProfile, pin_key: str, replacement: ViewRecord
) -> NavigationProfile:
    """Apply ``chooseDifferentView``: the pin now opens the replacement view.

    The pin keeps its key, its group, and its position — identity and
    placement are the user's arrangement; only the target changes. The label
    becomes the replacement view's name: the old label described the old
    view, and keeping it would leave a pin that lies about where it goes.
    Raises :class:`UnknownPinError` for a missing pin and ``ValueError`` for
    a soft-deleted replacement — repairing a broken pin with a removed view
    would recreate the break on the next render.
    """
    pin = _require_pin(profile, pin_key)
    if replacement.deleted_at is not None:
        raise ValueError(f"view '{replacement.view_key}' is removed and cannot repair a pin")
    repointed = Pin(
        pin_key=pin.pin_key,
        panel_key=replacement.panel_key,
        view_key=replacement.view_key,
        label=replacement.name,
        group=pin.group,
    )
    log.info(
        "pin repointed to a different view",
        extra={
            "context": {
                "pinKey": pin_key,
                "fromViewKey": pin.view_key,
                "toViewKey": replacement.view_key,
            }
        },
    )
    return NavigationProfile(
        presentation=profile.presentation,
        pins=tuple(repointed if p.pin_key == pin_key else p for p in profile.pins),
    )
