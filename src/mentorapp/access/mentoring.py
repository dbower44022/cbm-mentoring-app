"""Mentor access: the seven REQ-071 areas as granted data sources (WTK-167/176/186).

REQ-071 names the mentor's areas — Contacts, Companies, Clients,
Engagements, Sessions, Resources, Events — and this module realizes them
with the EXISTING machinery only, inventing no second permission model:

- **Each area is a data source** (:data:`MENTOR_DATA_SOURCES`): a stored
  SELECT over the generated read views, seeded by
  :func:`seed_mentor_access` (migration 0014 calls it; re-runs reconcile).
  Area visibility IS data-source permission
  (:mod:`mentorapp.access.areas` over the REQ-006 grant boundary), so
  :data:`MENTOR_AREAS` simply pairs each area key with its source key.
- **Row confinement is the REQ-019 injected filter, never a WHERE the
  author could drop.** The engagement-bearing sources declare
  ``userRowFilter`` and reference ``:currentUserID`` on the mentor anchor's
  ``userID`` pairing column (``crmMentorRef.userID`` — the WTK-167 pairing);
  ``execute_admin_sql`` binds the session user server-side and rejects a
  caller-supplied one, so mentor A structurally cannot read mentor B's
  engagements (WTK-186). Resources and events are unscoped: the library and
  calendar are org-wide reads (REQ-084/REQ-085).
- **Grants are role-keyed** (``dataSourceRoleGrant``): every mentor source
  is granted to :data:`MENTOR_ROLE` and :data:`LEADERSHIP_ROLE`; the
  across-mentors engagement source (:data:`DS_LEADERSHIP_ENGAGEMENTS`,
  the unscoped :func:`~mentorapp.storage.triage.engagement_triage_sql`
  variant) is granted to leadership only — leadership sees across mentors,
  mentors see themselves.
- **Staff maintenance is a capability, not a source grant** (REQ-084/085):
  :func:`authorize_resource_management` / :func:`authorize_event_management`
  gate writes by the ``resource.manage``/``event.manage`` keys through the
  one capability boundary (:mod:`mentorapp.access.views`), exactly like
  workprocess registration. Mentors hold neither, which is what makes the
  resources and events areas read-only for them.

Role names are the CRM's team names verbatim: the identity bridge
(WTK-003) captures Espo team names as the session's roles with no
translation table, so the grant vocabulary must speak the same words.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.access.areas import AreaDescriptor
from mentorapp.access.grants import grant_data_source_role
from mentorapp.access.views import (
    CAP_EVENT_MANAGE,
    CAP_RESOURCE_MANAGE,
    CapabilityLookup,
    StoredCapabilityRegistry,
    authorize_capability,
)
from mentorapp.observability import get_logger
from mentorapp.storage import DataSource
from mentorapp.storage.triage import ENGAGEMENT_TRIAGE_COLUMNS, engagement_triage_sql

log = get_logger(__name__)

# The CRM staff-role (Espo team) names the grants are keyed by.
MENTOR_ROLE: Final = "Mentor"
LEADERSHIP_ROLE: Final = "Leadership"

# The seeded data-source keys — stable identifiers other records reference.
DS_MENTOR_CONTACTS: Final = "mentorContacts"
DS_MENTOR_COMPANIES: Final = "mentorCompanies"
DS_MENTOR_CLIENTS: Final = "mentorClients"
DS_MENTOR_ENGAGEMENTS: Final = "mentorEngagements"
DS_MENTOR_SESSIONS: Final = "mentorSessions"
DS_MENTOR_RESOURCES: Final = "mentorResources"
DS_MENTOR_EVENTS: Final = "mentorEvents"
DS_LEADERSHIP_ENGAGEMENTS: Final = "leadershipEngagements"

# REQ-071's seven areas, in the requirement's order. Area permission derives
# from the source grant (access.areas) — this tuple is the shell's input,
# never a second permission table.
MENTOR_AREAS: Final[tuple[AreaDescriptor, ...]] = (
    AreaDescriptor("contacts", DS_MENTOR_CONTACTS),
    AreaDescriptor("companies", DS_MENTOR_COMPANIES),
    AreaDescriptor("clients", DS_MENTOR_CLIENTS),
    AreaDescriptor("engagements", DS_MENTOR_ENGAGEMENTS),
    AreaDescriptor("sessions", DS_MENTOR_SESSIONS),
    AreaDescriptor("resources", DS_MENTOR_RESOURCES),
    AreaDescriptor("events", DS_MENTOR_EVENTS),
)

# The REQ-019 pairing column every scoped source filters on: the mentor
# anchor's app-user linkage, exposed by vwCrmMentorRef.
_USER_FILTER_COLUMN: Final = "userID"

# Mentor-scoped SQL bodies. All read the generated views (DB-S9); each
# scoped body references :currentUserID exactly once, on the pairing column.
# The Contacts area serves the engagement-designated contacts the app store
# owns (REQ-072 working data); browsing the CRM's full contact book stays a
# CRM read outside the app read surface.
_CONTACTS_SQL: Final = (
    'SELECT DISTINCT e."primaryContactName", e."primaryContactEmail",\n'
    '       e."primaryContactCrmID", m."userID"\n'
    'FROM "vwEngagement" e\n'
    'JOIN "vwCrmMentorRef" m ON m."crmMentorRefID" = e."crmMentorRefID"\n'
    'WHERE m."userID" = :currentUserID AND e."primaryContactName" IS NOT NULL\n'
    'ORDER BY e."primaryContactName"'
)

_COMPANIES_SQL: Final = (
    'SELECT DISTINCT co."crmCompanyRefID", co."crmCompanyID", m."userID"\n'
    'FROM "vwCrmCompanyRef" co\n'
    'JOIN "vwClient" c ON c."crmCompanyRefID" = co."crmCompanyRefID"\n'
    'JOIN "vwEngagement" e ON e."clientID" = c."clientID"\n'
    'JOIN "vwCrmMentorRef" m ON m."crmMentorRefID" = e."crmMentorRefID"\n'
    'WHERE m."userID" = :currentUserID'
)

_CLIENTS_SQL: Final = (
    'SELECT DISTINCT c."clientID", c."crmCompanyRefID", co."crmCompanyID",\n'
    '       c."clientSince", c."clientProgram", c."clientReferralSource",\n'
    '       c."clientStage", m."userID"\n'
    'FROM "vwClient" c\n'
    'JOIN "vwCrmCompanyRef" co ON co."crmCompanyRefID" = c."crmCompanyRefID"\n'
    'JOIN "vwEngagement" e ON e."clientID" = c."clientID"\n'
    'JOIN "vwCrmMentorRef" m ON m."crmMentorRefID" = e."crmMentorRefID"\n'
    'WHERE m."userID" = :currentUserID'
)

_SESSIONS_SQL: Final = (
    'SELECT s."sessionID", s."engagementID", e."engagementName", s."scheduledAt",\n'
    '       s."sessionStatusLabel", s."conferenceLink", s."sessionNotes",\n'
    '       s."actionItems", m."userID"\n'
    'FROM "vwSession" s\n'
    'JOIN "vwEngagement" e ON e."engagementID" = s."engagementID"\n'
    'JOIN "vwCrmMentorRef" m ON m."crmMentorRefID" = e."crmMentorRefID"\n'
    'WHERE m."userID" = :currentUserID\n'
    'ORDER BY s."scheduledAt"'
)

_RESOURCES_SQL: Final = (
    'SELECT r."resourceID", r."resourceTitle", r."resourceKindLabel",\n'
    '       r."resourceLocation", r."resourceDescription"\n'
    'FROM "vwResource" r\n'
    'ORDER BY r."resourceTitle"'
)

_EVENTS_SQL: Final = (
    'SELECT ev."eventID", ev."eventTitle", ev."startsAt", ev."eventLocation",\n'
    '       ev."eventAudience"\n'
    'FROM "vwEvent" ev\n'
    'ORDER BY ev."startsAt"'
)


@dataclass(frozen=True)
class MentorSourceSpec:
    """One seeded area source: its stored form plus the roles granted on it."""

    data_source_key: str
    data_source_name: str
    sql_text: str
    exposed_fields: tuple[str, ...]
    granted_roles: tuple[str, ...]
    # Non-null = the REQ-019 user-scoped declaration (the view column bound
    # server-side); None = an org-wide read.
    user_row_filter: str | None = None


_MENTOR_AND_LEADERSHIP: Final = (MENTOR_ROLE, LEADERSHIP_ROLE)

MENTOR_DATA_SOURCES: Final[tuple[MentorSourceSpec, ...]] = (
    MentorSourceSpec(
        DS_MENTOR_CONTACTS,
        "My Contacts",
        _CONTACTS_SQL,
        ("primaryContactName", "primaryContactEmail", "primaryContactCrmID", "userID"),
        _MENTOR_AND_LEADERSHIP,
        user_row_filter=_USER_FILTER_COLUMN,
    ),
    MentorSourceSpec(
        DS_MENTOR_COMPANIES,
        "My Companies",
        _COMPANIES_SQL,
        ("crmCompanyRefID", "crmCompanyID", "userID"),
        _MENTOR_AND_LEADERSHIP,
        user_row_filter=_USER_FILTER_COLUMN,
    ),
    MentorSourceSpec(
        DS_MENTOR_CLIENTS,
        "My Clients",
        _CLIENTS_SQL,
        (
            "clientID",
            "crmCompanyRefID",
            "crmCompanyID",
            "clientSince",
            "clientProgram",
            "clientReferralSource",
            "clientStage",
            "userID",
        ),
        _MENTOR_AND_LEADERSHIP,
        user_row_filter=_USER_FILTER_COLUMN,
    ),
    MentorSourceSpec(
        DS_MENTOR_ENGAGEMENTS,
        # REQ-072's own name for the mentor's landing triage view — the PI-010
        # surfaces ruling: mentors land on Engagements "My Active Engagements".
        "My Active Engagements",
        engagement_triage_sql(mentor_scoped=True),
        (*ENGAGEMENT_TRIAGE_COLUMNS, "userID"),
        _MENTOR_AND_LEADERSHIP,
        user_row_filter=_USER_FILTER_COLUMN,
    ),
    MentorSourceSpec(
        DS_MENTOR_SESSIONS,
        "My Sessions",
        _SESSIONS_SQL,
        (
            "sessionID",
            "engagementID",
            "engagementName",
            "scheduledAt",
            "sessionStatusLabel",
            "conferenceLink",
            "sessionNotes",
            "actionItems",
            "userID",
        ),
        _MENTOR_AND_LEADERSHIP,
        user_row_filter=_USER_FILTER_COLUMN,
    ),
    MentorSourceSpec(
        DS_MENTOR_RESOURCES,
        "Resources",
        _RESOURCES_SQL,
        (
            "resourceID",
            "resourceTitle",
            "resourceKindLabel",
            "resourceLocation",
            "resourceDescription",
        ),
        _MENTOR_AND_LEADERSHIP,
    ),
    MentorSourceSpec(
        DS_MENTOR_EVENTS,
        "Events",
        _EVENTS_SQL,
        ("eventID", "eventTitle", "startsAt", "eventLocation", "eventAudience"),
        _MENTOR_AND_LEADERSHIP,
    ),
    MentorSourceSpec(
        DS_LEADERSHIP_ENGAGEMENTS,
        "All Engagements",
        engagement_triage_sql(mentor_scoped=False),
        ENGAGEMENT_TRIAGE_COLUMNS,
        (LEADERSHIP_ROLE,),
    ),
)


def seed_mentor_access(session: Session) -> None:
    """Seed/reconcile the area data sources and their role grants (WTK-167/176).

    Idempotent and convergent, the registry-seed philosophy: a missing
    source is inserted, an existing one has its stored SQL, scoping
    declaration, and exposed fields reconciled to the source-controlled spec
    (these are platform-seeded sources — their truth is this module, not the
    row), and the seeded role grants are (re)issued when absent — the areas
    are the mentor product surface, so a seed run restores them even after a
    mistaken revoke; deny-by-default still holds for every role this module
    does not name. Flushes but never commits — the caller (a migration or
    startup wiring) owns the transaction.
    """
    for spec in MENTOR_DATA_SOURCES:
        row = session.scalars(
            select(DataSource).where(
                DataSource.data_source_key == spec.data_source_key,
                DataSource.deleted_at.is_(None),
            )
        ).one_or_none()
        if row is None:
            row = DataSource(
                data_source_key=spec.data_source_key,
                data_source_name=spec.data_source_name,
                data_source_sql=spec.sql_text,
                user_row_filter=spec.user_row_filter,
                exposed_fields=list(spec.exposed_fields),
            )
            session.add(row)
            session.flush()
        else:
            row.data_source_name = spec.data_source_name
            row.data_source_sql = spec.sql_text
            row.user_row_filter = spec.user_row_filter
            row.exposed_fields = list(spec.exposed_fields)
        for role_name in spec.granted_roles:
            grant_data_source_role(
                session, data_source_key=spec.data_source_key, role_name=role_name
            )
    session.flush()
    log.info(
        "mentor area data sources seeded",
        extra={"context": {"sourceCount": len(MENTOR_DATA_SOURCES)}},
    )


def authorize_resource_management(lookup: CapabilityLookup, *, user_id: uuid.UUID) -> None:
    """REQ-084's gate, by name: only ``resource.manage`` holders maintain the
    library. Maintaining only — reading resources stays the granted
    data-source boundary, which is why mentors can read what they cannot edit."""
    authorize_capability(lookup, user_id=user_id, capability=CAP_RESOURCE_MANAGE)


def authorize_event_management(lookup: CapabilityLookup, *, user_id: uuid.UUID) -> None:
    """REQ-085's gate, by name: only ``event.manage`` holders define or edit
    events; every mentor-facing surface over the events source is read-only."""
    authorize_capability(lookup, user_id=user_id, capability=CAP_EVENT_MANAGE)


def authorize_stored_resource_management(session: Session, *, user_id: uuid.UUID) -> None:
    """The stored form of the REQ-084 gate — the ``accessGrant`` rows decide,
    so revoking ``resource.manage`` changes the very next attempt."""
    authorize_resource_management(StoredCapabilityRegistry(session), user_id=user_id)


def authorize_stored_event_management(session: Session, *, user_id: uuid.UUID) -> None:
    """The stored form of the REQ-085 gate, mirroring the workprocess shape."""
    authorize_event_management(StoredCapabilityRegistry(session), user_id=user_id)
