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
- ``GET /shell/bell`` / ``POST /shell/bell/read`` are the notification bell
  the header renders (REQ-014, WTK-023): the session user's unread entries
  (the badge count rides ``meta.unreadCount``), and the read stamp the moment
  the user views them. Emission lives in the worker
  (:mod:`mentorapp.automation.worker`); this surface only reads and stamps.

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

from mentorapp.access.areas import AreaDescriptor, is_area_accessible
from mentorapp.access.grants import GrantLookup
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.envelope import Envelope, ok
from mentorapp.api.errors import RecordNotFoundError
from mentorapp.observability import get_logger
from mentorapp.storage import Notification, UserPreference, utcnow
from mentorapp.ui.home_panel import (
    STARTUP_PREFERENCE_KEY,
    StartupChoice,
    resolve_startup_panel,
)
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


def _area_entries(catalog: ShellCatalog, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """The server-declared Areas (WTK-233, REQ-071): accessible panels, in order.

    Every catalog panel with a data source is an area candidate; visibility
    is the quiet form of the one grant boundary (WTK-025 — panel permission
    IS data-source permission). Each entry names the panel's first live view
    so activating an area opens something concrete; the panel's own grid
    read re-decides the active view for the caller anyway.
    """
    grants = catalog.grants()
    user_roles = catalog.user_roles(user_id)
    views = catalog.views()
    entries: list[dict[str, Any]] = []
    for panel in catalog.panels():
        if panel.data_source_key is None:
            continue  # Home is the rail's fixed anchor, never an area entry.
        area = AreaDescriptor(panel.panel_key, panel.data_source_key)
        if not is_area_accessible(area, grants=grants, user_roles=user_roles):
            continue
        default_view = next(
            (v for v in views if v.panel_key == panel.panel_key and v.deleted_at is None),
            None,
        )
        entries.append(
            {
                "panelKey": panel.panel_key,
                "label": panel.title,
                "viewKey": default_view.view_key if default_view is not None else None,
            }
        )
    return entries


def _startup_payload(
    session: Session, user_id: uuid.UUID, accessible_panel_keys: list[str]
) -> dict[str, Any]:
    """Where this window should land (REQ-011/REQ-015, the REQ-072 default).

    The ``shell.startup`` preference document under the REQ-060 pair (own
    row overrides the org default — the mentor deployment seeds an org
    default that lands on the engagements panel, Doug's REQ-072 ruling).
    Parsing degrades, never fails: an unknown choice or panel resolves
    through :func:`resolve_startup_panel`, landing on Home with the educate
    notice rather than a blank screen.
    """
    rows = session.scalars(
        select(UserPreference)
        .where(UserPreference.deleted_at.is_(None))
        .where(UserPreference.preference_key == STARTUP_PREFERENCE_KEY)
        .where(or_(UserPreference.user_id == user_id, UserPreference.user_id.is_(None)))
    ).all()
    row = next((r for r in rows if r.user_id == user_id), None)
    row = row or next((r for r in rows if r.user_id is None), None)
    if row is None:
        return {"panelKey": HOME_PANEL_KEY, "notice": None}
    document = row.preference_value or {}
    try:
        choice = StartupChoice(document.get("choice"))
    except ValueError:
        choice = StartupChoice.HOME
    last_panel = document.get("lastPanelKey")
    target = resolve_startup_panel(
        choice,
        last_panel if isinstance(last_panel, str) else None,
        accessible_panel_keys,
    )
    return {
        "panelKey": target.panel_key,
        "notice": target.notice.as_payload() if target.notice is not None else None,
    }


@router.get("/shell")
def get_shell(session: _SessionDep, user_id: _UserDep, catalog: _CatalogDep) -> Envelope:
    """Compose the shell: headers, navigation, the Areas, and the startup target.

    ``data.mainWindow``/``data.popOut`` carry the one WTK-026 header
    declaration for each window kind (pop-outs are record windows, not panel
    hosts — REQ-012). ``data.navigation`` renders the stored pin set for its
    stored presentation with every pin present and broken ones marked
    (REQ-010/REQ-015). ``data.areas`` is the server-declared area list
    (WTK-233 — membership is the grant boundary's decision, never the
    client's), and ``data.startup`` is where this boot should land: the
    ``shell.startup`` preference resolved against the accessible panels
    (mentors default to the engagements panel per the REQ-072 ruling).
    Fails 500 when the catalog provider is unwired; 422 without
    ``X-User-ID``.
    """
    profile = _navigation_profile(session, user_id)
    resolved = resolve_navigation(
        profile,
        catalog=catalog,
        grants=catalog.grants(),
        user_id=user_id,
        user_roles=catalog.user_roles(user_id),
    )
    areas = _area_entries(catalog, user_id)
    startup = _startup_payload(session, user_id, [entry["panelKey"] for entry in areas])
    return ok(
        data={
            "mainWindow": _header_payload(header_for_window(is_pop_out=False)),
            "popOut": _header_payload(header_for_window(is_pop_out=True)),
            "homePanelKey": HOME_PANEL_KEY,
            "areas": areas,
            "startup": startup,
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


def _unread_notifications(session: Session, user_id: uuid.UUID) -> list[Notification]:
    """The session user's live unread bell entries, newest first (REQ-014).

    ``createdAt`` with the UUIDv7 ID as tiebreak — the DB-S8 sort shape, and
    insertion order for same-instant writes. The predicate is exactly the
    ``ix_notification_unread`` badge scan.
    """
    return list(
        session.scalars(
            select(Notification)
            .where(Notification.deleted_at.is_(None))
            .where(Notification.user_id == user_id)
            .where(Notification.read_at.is_(None))
            .order_by(Notification.created_at.desc(), Notification.notification_id.desc())
        )
    )


def _bell_entry_payload(entry: Notification) -> dict[str, Any]:
    return {
        "notificationID": str(entry.notification_id),
        "notificationType": entry.notification_type,
        "notificationMessage": entry.notification_message,
        "jobID": str(entry.job_id) if entry.job_id is not None else None,
        "createdAt": entry.created_at.isoformat(),
    }


@router.get("/shell/bell")
def get_bell(session: _SessionDep, user_id: _UserDep) -> Envelope:
    """The bell dropdown: the session user's unread entries (REQ-014).

    ``meta.unreadCount`` is the badge number — the header may poll this
    endpoint for the count alone. Reading does NOT stamp ``readAt``: a GET
    stays safe to repeat, and the client acknowledges the view explicitly
    via ``POST /shell/bell/read`` when the dropdown opens.
    """
    entries = _unread_notifications(session, user_id)
    return ok(
        data={"entries": [_bell_entry_payload(entry) for entry in entries]},
        meta={"unreadCount": len(entries)},
    )


@router.post("/shell/bell/read")
def mark_bell_read(session: _SessionDep, user_id: _UserDep) -> Envelope:
    """Viewing the bell stamps every unread entry (REQ-014).

    A versioned update like any other write (DB-S4), never a delete — the
    entries stay in the notification table, only the badge predicate stops
    matching them. Idempotent: a second view finds nothing unread and stamps
    nothing, so ``data.markedRead`` reports what THIS view cleared.
    """
    viewed_at = utcnow()
    entries = _unread_notifications(session, user_id)
    for entry in entries:
        entry.read_at = viewed_at
        entry.modified_by = user_id
    session.commit()
    return ok(data={"markedRead": len(entries)}, meta={"unreadCount": 0})
