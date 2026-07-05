"""``/shell`` — the window-shell surface: header, quick-open, navigation (WTK-035).

The integration of the WTK-026/020/028 designs into the app framework
(REQ-009, REQ-010, REQ-012): no frontend shell exists yet (PI-002), so the
shell renders exactly what these endpoints serve — ``mentorapp.ui`` stays the
one home for the behavior, this router only speaks it over the envelope.

- ``GET /shell`` composes what every window needs before it draws anything:
  the two header declarations (``mainWindow`` with navigation, ``popOut``
  without — REQ-009/REQ-012, one :class:`~mentorapp.ui.shell_header.StandardHeader`
  definition, never two), and the user's navigation rendered for their chosen
  presentation with broken pins marked, never dropped (REQ-010/REQ-015).
- ``GET /shell/quick-open`` is the Ctrl+K palette: every panel and view the
  user can reach (REQ-009), narrowed by ``q`` with prefix matches first.
- ``POST /shell/navigation/pins/{pinKey}/open`` activates one pin at click
  time: a healthy pin answers with its panel opening, a broken one with the
  explanation dialog and its two choices (REQ-015) — never a dead control.

The navigation profile is SETTINGS: it lives as the ``navigation`` preference
document under the REQ-060 pair (own row overrides the org default, exactly
as ``GET /preferences`` resolves), read here and written only through
``PUT /preferences/navigation`` — applying a dialog choice
(:func:`~mentorapp.ui.navigation_shell.remove_pin` /
:func:`~mentorapp.ui.navigation_shell.repoint_pin`) produces the next
document for that same seam, never a second persistence path.

Panel/view lookup follows the home-router seam pattern (fail loudly until
wired; tests and deployments override :func:`get_shell_catalog`): the panel
catalog lands with its own planning item, and an empty in-process default
would render every user's navigation broken and the palette empty.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from mentorapp.access.grants import GrantLookup
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.envelope import Envelope, ok
from mentorapp.api.errors import RecordNotFoundError
from mentorapp.observability import get_logger
from mentorapp.storage import UserPreference
from mentorapp.ui.navigation import (
    HOME_PANEL_KEY,
    NAVIGATION_PREFERENCE_KEY,
    NavigationPresentation,
    NavigationProfile,
    Panel,
    Pin,
    ViewRecord,
    navigation_profile_from_document,
)
from mentorapp.ui.navigation_shell import (
    BrokenPinDialog,
    NavigationRendering,
    open_pin,
    render_navigation,
    resolve_navigation,
)
from mentorapp.ui.shell_header import (
    StandardHeader,
    header_for_window,
    quick_open_entries,
    search_quick_open,
)

log = get_logger(__name__)

router = APIRouter()

_PIN_ENTITY = "navigationPin"


class ShellCatalog(Protocol):
    """What composing the shell needs to know about panels, views, and grants.

    ``panel``/``view`` make this a
    :class:`~mentorapp.ui.navigation.NavigationCatalog`, so pin resolution
    asks the SAME lookup the palette lists from — the two can never disagree
    about what exists. Grants and roles ride the seam too: the REQ-006
    boundary is derived by the access area (WTK-025), never re-derived here.
    """

    def panel(self, panel_key: str) -> Panel | None:
        """The panel, or ``None`` when no such panel exists."""
        ...

    def view(self, view_key: str) -> ViewRecord | None:
        """The view INCLUDING soft-deleted tombstones; ``None`` if unknown."""
        ...

    def panels(self) -> Sequence[Panel]:
        """Every panel the app hosts (permissioning happens per user)."""
        ...

    def views(self) -> Sequence[ViewRecord]:
        """Every view, tombstones included (the palette filters, pins explain)."""
        ...

    def grants(self) -> GrantLookup:
        """The one grant boundary (REQ-006)."""
        ...

    def user_roles(self, user_id: uuid.UUID) -> frozenset[str]:
        """The user's role names, for the grant decision."""
        ...


def get_shell_catalog() -> ShellCatalog:
    """Provide the panel/view/grant catalog; wiring binds it, tests override it.

    Fail-loud, never an empty default: a missing binding must read as a
    deployment error, not as every pin broken and an empty palette.
    """
    raise RuntimeError(
        "shell catalog provider is not wired; install shell wiring or "
        "override get_shell_catalog."
    )


_SessionDep = Annotated[Session, Depends(get_session)]
_UserDep = Annotated[uuid.UUID, Depends(get_current_user_id)]
_CatalogDep = Annotated[ShellCatalog, Depends(get_shell_catalog)]


def _navigation_profile(session: Session, user_id: uuid.UUID) -> NavigationProfile:
    """The user's stored navigation — the REQ-060 read rule, one document.

    Own row overrides the org default, exactly as ``GET /preferences``
    resolves. No row means the built-in default (tabs, no pins) — a normal
    first-login state, and parsing degrades rather than fails on stale
    documents (WTK-020), so this never leaves a window without navigation.
    """
    rows = session.scalars(
        select(UserPreference)
        .where(UserPreference.deleted_at.is_(None))
        .where(UserPreference.preference_key == NAVIGATION_PREFERENCE_KEY)
        .where(or_(UserPreference.user_id == user_id, UserPreference.user_id.is_(None)))
    ).all()
    row = next((r for r in rows if r.user_id == user_id), None)
    row = row or next((r for r in rows if r.user_id is None), None)
    if row is None:
        return NavigationProfile()
    return navigation_profile_from_document(row.preference_value)


def _header_payload(header: StandardHeader) -> dict[str, Any]:
    return {
        "left": list(header.left),
        "right": list(header.right),
        "accountMenu": [{"key": item.key, "label": item.label} for item in header.account_menu],
        "quickOpenShortcut": header.quick_open_shortcut,
        "hasNavigation": header.has_navigation,
    }


def _rendering_payload(rendering: NavigationRendering) -> dict[str, Any]:
    return {
        "presentation": rendering.presentation.value,
        "presentations": [p.value for p in NavigationPresentation],
        "groups": [
            {
                "label": group.label,
                "items": [
                    {
                        "pinKey": item.pin_key,
                        "label": item.label,
                        "panelKey": item.panel_key,
                        "viewKey": item.view_key,
                        "isBroken": item.is_broken,
                    }
                    for item in group.items
                ],
            }
            for group in rendering.groups
        ],
    }


@router.get("/shell")
def get_shell(session: _SessionDep, user_id: _UserDep, catalog: _CatalogDep) -> Envelope:
    """Compose the shell: both headers plus the user's rendered navigation.

    ``data.mainWindow``/``data.popOut`` carry the one WTK-026 header
    declaration for each window kind (pop-outs are record windows, not panel
    hosts — REQ-012). ``data.navigation`` renders the stored pin set for its
    stored presentation with every pin present and broken ones marked
    (REQ-010/REQ-015). Fails 500 when the catalog provider is unwired; 422
    without ``X-User-ID``.
    """
    profile = _navigation_profile(session, user_id)
    resolved = resolve_navigation(
        profile,
        catalog=catalog,
        grants=catalog.grants(),
        user_id=user_id,
        user_roles=catalog.user_roles(user_id),
    )
    return ok(
        data={
            "mainWindow": _header_payload(header_for_window(is_pop_out=False)),
            "popOut": _header_payload(header_for_window(is_pop_out=True)),
            "homePanelKey": HOME_PANEL_KEY,
            "navigation": _rendering_payload(render_navigation(profile.presentation, resolved)),
        }
    )


@router.get("/shell/quick-open")
def get_quick_open(
    user_id: _UserDep,
    catalog: _CatalogDep,
    q: Annotated[str, Query()] = "",
) -> Envelope:
    """The Ctrl+K palette: every reachable destination, narrowed by ``q``.

    Reachability is the one grant boundary in its quiet form (listing is not
    an open attempt); soft-deleted views simply are not destinations. An
    empty ``q`` serves the full catalog — panels first, then views, each
    alphabetical; a query ranks prefix matches before substring matches.
    """
    entries = quick_open_entries(
        catalog.panels(),
        catalog.views(),
        grants=catalog.grants(),
        user_id=user_id,
        user_roles=catalog.user_roles(user_id),
    )
    matches = search_quick_open(entries, q)
    return ok(
        data={
            "entries": [
                {
                    "kind": entry.kind.value,
                    "label": entry.label,
                    "panelKey": entry.panel_key,
                    "viewKey": entry.view_key,
                }
                for entry in matches
            ]
        },
        meta={"totalCount": len(matches)},
    )


def _require_pin(profile: NavigationProfile, pin_key: str) -> Pin:
    for pin in profile.pins:
        if pin.pin_key == pin_key:
            return pin
    # 404, not 422: another window may have removed the pin — a normal
    # cross-window race the shell answers by refreshing its navigation.
    raise RecordNotFoundError(_PIN_ENTITY, pin_key)


@router.post("/shell/navigation/pins/{pin_key}/open")
def open_navigation_pin(
    pin_key: str, session: _SessionDep, user_id: _UserDep, catalog: _CatalogDep
) -> Envelope:
    """Activate one pin: its panel with its view active, or the dialog (REQ-015).

    Resolution happens now, at click time — a pin that broke after the
    navigation was drawn explains itself (``data.dialog``: the educate
    message plus the two choices) instead of opening a dead panel; one
    repaired since simply opens (``data.opened``). Exactly one of the two is
    set. 404 for a pin no longer in the profile.
    """
    pin = _require_pin(_navigation_profile(session, user_id), pin_key)
    outcome = open_pin(
        pin,
        catalog=catalog,
        grants=catalog.grants(),
        user_id=user_id,
        user_roles=catalog.user_roles(user_id),
    )
    if isinstance(outcome, BrokenPinDialog):
        return ok(
            data={
                "opened": None,
                "dialog": {
                    "pinKey": outcome.pin.pin_key,
                    "reason": outcome.break_.reason.value,
                    "message": outcome.message.as_payload(),
                    "choices": [choice.value for choice in outcome.choices],
                },
            }
        )
    return ok(
        data={
            "opened": {"panelKey": outcome.panel_key, "viewKey": outcome.view_key},
            "dialog": None,
        }
    )
