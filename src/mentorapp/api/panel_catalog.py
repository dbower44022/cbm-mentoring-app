"""The mentor panel catalog: REQ-071's areas as servable panels (WTK-233).

The binding the fail-loud catalog seams have waited for. Each of the seven
REQ-071 areas (:data:`~mentorapp.access.mentoring.MENTOR_AREAS`) becomes one
grid panel served over its SEEDED data source — the panel definitions are
source-controlled here (they are product surface, like the sources
themselves), while permission stays entirely the REQ-006 grant boundary:
a panel is visible/openable exactly when the caller's roles cover its data
source, decided live against the stored grants on every ask.

Three consumers bind to this one catalog:

- The ``/panels`` router (:mod:`mentorapp.api.routers.panels`) serves each
  panel's grid view-model and rows from the specs here.
- :class:`MentorPanelCatalog` satisfies the home router's ``HomeCatalog``
  and the shell router's ``ShellCatalog`` protocols, so the Areas rail, the
  quick-open palette, pin resolution, and dashlet availability all derive
  from the SAME definitions — none of them can disagree about what exists.
- :class:`StoredSessionRoleSource` is the production ``RoleSource``: roles
  are session-scoped, captured from the CRM at login (WTK-001/003) onto
  ``authSession.sessionRoleNames`` — the newest live capture is the user's
  current role set; there is no user-role table to drift.

View keys are the data-source keys verbatim (one name, one meaning, DB-R2):
the frontend's action menus and domain previews key off the ACTIVE view's
``dataSourceKey``, so serving the seeded keys is what activates the pass-2
mentoring actions and the engagement preview. The engagements panel's first
view is REQ-072's "My Active Engagements" — the mentor landing view.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.access.grants import GrantLookup, StoredGrantRegistry, roles_cover_data_source
from mentorapp.access.mentoring import (
    DS_LEADERSHIP_ENGAGEMENTS,
    DS_MENTOR_CLIENTS,
    DS_MENTOR_COMPANIES,
    DS_MENTOR_CONTACTS,
    DS_MENTOR_ENGAGEMENTS,
    DS_MENTOR_EVENTS,
    DS_MENTOR_RESOURCES,
    DS_MENTOR_SESSIONS,
    MENTOR_DATA_SOURCES,
    MentorSourceSpec,
)

# The one role seam (the mentoring-router stance): declared by the
# workprocess router, bound once — this catalog consumes it, never forks it.
from mentorapp.api.grid_surface import AggregateSpec
from mentorapp.api.routers.workprocess import RoleSource
from mentorapp.observability import get_logger
from mentorapp.storage import AuthSession, UserPreference, as_utc
from mentorapp.storage.columns import ColumnSpec, FormattingRuleSpec, displayed_columns
from mentorapp.ui.home_panel import STARTUP_PREFERENCE_KEY
from mentorapp.ui.navigation import HOME_PANEL, Panel, PanelType, ViewRecord

log = get_logger(__name__)

# The engagements area's stable key — REQ-072's mentor landing panel.
ENGAGEMENTS_PANEL_KEY: Final = "engagements"

# The seeded source specs by key — the SQL, column declarations, and scoping
# declaration all come from the one source-controlled module.
SOURCE_SPECS: Final[dict[str, MentorSourceSpec]] = {
    spec.data_source_key: spec for spec in MENTOR_DATA_SOURCES
}


@dataclass(frozen=True)
class PanelViewSpec:
    """One view of one panel: a seeded data source with its grid identity.

    ``view_key`` IS the data-source key (the frontend's preview seam and
    action fold match on it). ``record_id_field``/``title_field`` name which
    exposed columns serve as each row's identity and display title —
    explicit per source because the sources project different shapes.
    ``aggregates`` is the view's own footer declaration (FND-019 — a footer
    aggregate is a VIEW property), speaking the one ``AggregateSpec``
    vocabulary the ``/grids`` surface already validates against.
    """

    view_key: str
    label: str
    criteria: str
    record_id_field: str
    title_field: str
    aggregates: tuple[AggregateSpec, ...] = ()

    @property
    def data_source_key(self) -> str:
        """The seeded source this view reads — same value as the view key."""
        return self.view_key

    @property
    def columns(self) -> tuple[ColumnSpec, ...]:
        """The displayed columns, declared order — plumbing (scoping userID,
        row-identity keys) rides the rows but never renders (FND-909 D2)."""
        return displayed_columns(SOURCE_SPECS[self.data_source_key].columns)

    @property
    def column_names(self) -> tuple[str, ...]:
        """The displayed field names — what search/sort may address (REQ-020)."""
        return tuple(spec.field_name for spec in self.columns)

    @property
    def formatting_rules(self) -> tuple[FormattingRuleSpec, ...]:
        """The source's REQ-045 rules, evaluation order — the same declared
        path as the columns (FND-909 D7), so a view can never disagree with
        its source about how status values paint."""
        return SOURCE_SPECS[self.data_source_key].formatting_rules


@dataclass(frozen=True)
class PanelSpec:
    """One REQ-071 area as a hostable grid panel with its view list."""

    panel_key: str
    title: str
    views: tuple[PanelViewSpec, ...]

    @property
    def primary_source_key(self) -> str:
        """The source that carries the panel's permission (WTK-025's rule)."""
        return self.views[0].data_source_key


# The seven areas, in REQ-071's order — panel keys match MENTOR_AREAS keys
# exactly (one name, one meaning). The engagements panel carries BOTH triage
# views: "My Active Engagements" first (REQ-072 — the mentor landing view),
# "All Engagements" second, visible only to roles the leadership grant
# covers, so one panel serves both audiences without a second panel key.
MENTOR_PANELS: Final[tuple[PanelSpec, ...]] = (
    PanelSpec(
        "contacts",
        "Contacts",
        (
            PanelViewSpec(
                DS_MENTOR_CONTACTS,
                "My Contacts",
                "the contacts designated on your engagements",
                record_id_field="primaryContactCrmID",
                title_field="primaryContactName",
            ),
        ),
    ),
    PanelSpec(
        "companies",
        "Companies",
        (
            PanelViewSpec(
                DS_MENTOR_COMPANIES,
                "My Companies",
                "the companies behind your engagements",
                record_id_field="crmCompanyRefID",
                title_field="crmCompanyID",
            ),
        ),
    ),
    PanelSpec(
        "clients",
        "Clients",
        (
            PanelViewSpec(
                DS_MENTOR_CLIENTS,
                "My Clients",
                "the client roles your engagements serve",
                record_id_field="clientID",
                title_field="crmCompanyID",
            ),
        ),
    ),
    PanelSpec(
        ENGAGEMENTS_PANEL_KEY,
        "Engagements",
        (
            PanelViewSpec(
                DS_MENTOR_ENGAGEMENTS,
                "My Active Engagements",
                "your engagements, ordered for triage",
                record_id_field="engagementID",
                title_field="engagementName",
                # SKL-112's footer row needs a served aggregate to exist; the
                # triage views declare the one honest aggregate a triage set
                # carries — the engagement count (FND-909 D11).
                aggregates=(AggregateSpec("count", "engagementName"),),
            ),
            PanelViewSpec(
                DS_LEADERSHIP_ENGAGEMENTS,
                "All Engagements",
                "every engagement across mentors, ordered for triage",
                record_id_field="engagementID",
                title_field="engagementName",
                aggregates=(AggregateSpec("count", "engagementName"),),
            ),
        ),
    ),
    PanelSpec(
        "sessions",
        "Sessions",
        (
            PanelViewSpec(
                DS_MENTOR_SESSIONS,
                "My Sessions",
                "every session of your engagements, in time order",
                record_id_field="sessionID",
                title_field="engagementName",
            ),
        ),
    ),
    PanelSpec(
        "resources",
        "Resources",
        (
            PanelViewSpec(
                DS_MENTOR_RESOURCES,
                "Resources",
                "the staff-maintained resource library",
                record_id_field="resourceID",
                title_field="resourceTitle",
            ),
        ),
    ),
    PanelSpec(
        "events",
        "Events",
        (
            PanelViewSpec(
                DS_MENTOR_EVENTS,
                "Events",
                "staff-defined events, soonest first",
                record_id_field="eventID",
                title_field="eventTitle",
            ),
        ),
    ),
)

PANELS_BY_KEY: Final[dict[str, PanelSpec]] = {p.panel_key: p for p in MENTOR_PANELS}
VIEWS_BY_KEY: Final[dict[str, tuple[PanelSpec, PanelViewSpec]]] = {
    view.view_key: (panel, view) for panel in MENTOR_PANELS for view in panel.views
}

# Acronyms the humanized column labels must keep upper-cased; everything
# else title-cases per camelCase word. Derived labels, because the sources
# project view columns that carry no registry label of their own.
_LABEL_ACRONYMS: Final = {"id": "ID", "crm": "CRM"}

# camelCase splitter that keeps acronym runs whole: an upper run followed by
# a capitalized word, a normal capitalized word, a leading lower word, or a
# digit run ("crmCompanyID" -> crm / Company / ID).
_LABEL_WORDS: Final = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\b|$)|[A-Z]?[a-z]+|\d+")


def column_label(field_name: str) -> str:
    """A human column header from one wire field name.

    Splits camelCase, title-cases words, upper-cases known acronyms, and
    drops a trailing "Label" — ``engagementStatusLabel`` is the decoded
    status, so its header reads "Engagement Status".
    """
    words = _LABEL_WORDS.findall(field_name)
    labeled = [_LABEL_ACRONYMS.get(w.lower(), w[:1].upper() + w[1:]) for w in words]
    if len(labeled) > 1 and labeled[-1] == "Label":
        labeled = labeled[:-1]
    return " ".join(labeled)


# The REQ-072 ruling as an ORG-DEFAULT preference row (the REQ-060 pair):
# mentors land on the engagements panel. Expressed in the EXISTING startup
# vocabulary — "open where you left off; before any recorded last panel,
# engagements" — so when last-panel recording ships it takes over without a
# schema or vocabulary change, and any user's own row overrides it today.
MENTOR_STARTUP_DEFAULT: Final[dict[str, str]] = {
    "choice": "lastPanel",
    "lastPanelKey": ENGAGEMENTS_PANEL_KEY,
}


def seed_startup_default(session: Session) -> None:
    """Seed the org-default ``shell.startup`` row when none exists (WTK-233).

    Insert-if-absent, deliberately NOT reconciled on re-run (unlike the area
    sources): an org preference row is admin-editable data — an admin who
    later points the org default elsewhere must not have a seed sweep it
    back. Flushes but never commits — the caller owns the transaction.
    """
    existing = session.scalars(
        select(UserPreference)
        .where(UserPreference.deleted_at.is_(None))
        .where(UserPreference.preference_key == STARTUP_PREFERENCE_KEY)
        .where(UserPreference.user_id.is_(None))
    ).first()
    if existing is not None:
        return
    session.add(
        UserPreference(
            user_id=None,
            preference_key=STARTUP_PREFERENCE_KEY,
            preference_value=dict(MENTOR_STARTUP_DEFAULT),
        )
    )
    session.flush()
    log.info(
        "org-default startup preference seeded",
        extra={"context": {"lastPanelKey": ENGAGEMENTS_PANEL_KEY}},
    )


def granted_views(
    panel: PanelSpec, *, grants: GrantLookup, user_roles: frozenset[str]
) -> tuple[PanelViewSpec, ...]:
    """The panel's views this caller's roles may read, in declared order.

    Each view carries its own source, so the grant decides per view — a
    mentor sees "My Active Engagements" alone; leadership sees both.
    """
    return tuple(
        view
        for view in panel.views
        if roles_cover_data_source(
            grants, data_source_key=view.data_source_key, user_roles=user_roles
        )
    )


# --- The production catalog behind the home/shell seams --------------------------------


@dataclass
class StoredSessionRoleSource:
    """The production role source: the newest live login capture (WTK-001/003).

    Roles are session-scoped — captured from the CRM's team names at each
    login/reauth onto ``authSession.sessionRoleNames`` — so the user's
    current roles are their most recent live, un-ended session's capture.
    No live session (or none carrying roles) is the empty set: deny by
    default, never a guess.
    """

    session: Session

    def user_roles(self, user_id: uuid.UUID) -> frozenset[str]:
        rows = self.session.scalars(
            select(AuthSession)
            .where(AuthSession.deleted_at.is_(None))
            .where(AuthSession.user_id == user_id)
            .where(AuthSession.session_state != "ended")
        ).all()
        if not rows:
            return frozenset()
        newest = max(rows, key=lambda row: as_utc(row.created_at))
        return frozenset(newest.session_role_names or ())


@dataclass
class MentorPanelCatalog:
    """One catalog satisfying ``HomeCatalog`` AND ``ShellCatalog`` (WTK-233).

    The Areas rail (home), the palette/pins (shell), and the ``/panels``
    surface all read the same panel definitions and the same grant boundary,
    so no surface can disagree with another about what a user may open.
    Views are source-controlled, never soft-deleted — a pin can only break
    by access revocation, which the grant check reports.
    """

    session: Session
    roles: RoleSource

    # --- ShellCatalog -------------------------------------------------------

    def panel(self, panel_key: str) -> Panel | None:
        if panel_key == HOME_PANEL.panel_key:
            return HOME_PANEL
        spec = PANELS_BY_KEY.get(panel_key)
        if spec is None:
            return None
        return Panel(spec.panel_key, spec.title, PanelType.GRID, spec.primary_source_key)

    def view(self, view_key: str) -> ViewRecord | None:
        entry = VIEWS_BY_KEY.get(view_key)
        if entry is None:
            return None
        panel, view_spec = entry
        return ViewRecord(view_spec.view_key, view_spec.label, panel.panel_key)

    def panels(self) -> tuple[Panel, ...]:
        area_panels = tuple(
            Panel(spec.panel_key, spec.title, PanelType.GRID, spec.primary_source_key)
            for spec in MENTOR_PANELS
        )
        return (HOME_PANEL, *area_panels)

    def views(self) -> tuple[ViewRecord, ...]:
        return tuple(
            ViewRecord(view.view_key, view.label, panel.panel_key)
            for panel in MENTOR_PANELS
            for view in panel.views
        )

    def grants(self) -> GrantLookup:
        return StoredGrantRegistry(self.session)

    def user_roles(self, user_id: uuid.UUID) -> frozenset[str]:
        return self.roles.user_roles(user_id)

    # --- HomeCatalog --------------------------------------------------------

    def accessible_panel_keys(self, user_id: uuid.UUID) -> tuple[str, ...]:
        """Home first (the system default), then the granted areas in rail order."""
        grants = self.grants()
        user_roles = self.user_roles(user_id)
        accessible = tuple(
            spec.panel_key
            for spec in MENTOR_PANELS
            if granted_views(spec, grants=grants, user_roles=user_roles)
        )
        log.info(
            "panel catalog derived accessible panels",
            extra={"context": {"userID": str(user_id), "accessibleCount": len(accessible)}},
        )
        return (HOME_PANEL.panel_key, *accessible)

    def available_view_keys(self, user_id: uuid.UUID) -> frozenset[str]:
        """View keys the user may render as dashlets — grant-derived like panels."""
        grants = self.grants()
        user_roles = self.user_roles(user_id)
        return frozenset(
            view.view_key
            for spec in MENTOR_PANELS
            for view in granted_views(spec, grants=grants, user_roles=user_roles)
        )
