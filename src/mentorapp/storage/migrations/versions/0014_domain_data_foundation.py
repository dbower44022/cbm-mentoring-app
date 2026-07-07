"""PI-010 domain data foundation (WTK-164/165/166/173/174/175/176/182).

The mentoring domain reconciled onto PI-009's delivery — one canonical home
per concept (SKL-122), extended or renamed, never duplicated:

- ``crmClientRef`` → ``crmCompanyRef`` (REQ-086): the CRM-mastered record is
  THE organization; the *client* and *partner* roles become the new
  app-owned 1:1 subclass tables ``client``/``partner``.
- ``crmEngagementRef`` → ``engagement``: PI-010 moves the engagement's
  working home app-side (REQ-075 status as option-set data, REQ-072
  server-side triage) — the anchor's rows are carried over, gaining the
  working columns; ``crmEngagementID`` becomes the optional CRM anchor.
- ``sessionLog`` → ``session`` (REQ-074/079/082, WTK-182): ``sessionLogDate``
  → ``scheduledAt``, ``sessionLogSummary`` → ``sessionNotes`` (now nullable —
  sessions are scheduled before they happen), plus status, conference link,
  action items, and the transcript retention columns.
- ``meetingNote``/``nextStep`` are retired: REQ-074 puts notes ON sessions
  and REQ-082 forbids structured task records — both concepts now live on
  the session's rich-text fields. Dropping the tables is safe because no
  production data exists (the entities shipped in 0007 with no feature
  surface of their own); their registry rows are soft-retired, not deleted.
- ``crmMentorRef`` gains the nullable ``userID`` pairing to ``appUser`` —
  the REQ-019 scoping column the mentor data sources filter on (WTK-167).
  The table is rebuilt (new table + copy + rename) because SQLite cannot ADD
  a foreign-keyed column in place without recreating the table.
- New app-owned ``resource`` (REQ-084) and ``event`` (REQ-085) tables.
- Seeds: registry rows for every touched entity in this same change-set
  (REQ-050) — which also creates the REQ-075/session/resource option sets.

Registry fixups run BEFORE the seed so pre-0014 databases carry their rows
forward under the new names (fresh chains already seed the new shapes at
0006/0007 and the fixups match nothing). Same structural rules as 0001..0013.

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from mentorapp.storage.base import utcnow
from mentorapp.storage.crm_refs import CrmCompanyRef, CrmMentorRef
from mentorapp.storage.mentoring import (
    Client,
    Engagement,
    Event,
    MentoringSession,
    Partner,
    Resource,
)
from mentorapp.storage.registry_seed import seed_built_in_registry

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

# Mirrors storage.base.JsonValue: JSONB on Postgres, plain JSON on SQLite.
_JSON_OBJECT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_LIVE = sa.text('"deletedAt" IS NULL')

_SEEDED_ENTITIES = (
    Client,
    CrmCompanyRef,
    CrmMentorRef,
    Engagement,
    Event,
    MentoringSession,
    Partner,
    Resource,
)

_STRUCTURAL_NAMES = (
    "createdAt",
    "createdBy",
    "modifiedAt",
    "modifiedBy",
    "deletedAt",
    "deletedBy",
    "rowVersion",
    "customAttributes",
)
_STRUCTURAL_COLUMNS_SQL = ", ".join(f'"{name}"' for name in _STRUCTURAL_NAMES)

# entityType renames carried into the registry (all rows — user-defined
# custom attributes travel with their entity).
_RENAMED_ENTITY_TYPES = (
    ("crmClientRef", "crmCompanyRef"),
    ("crmEngagementRef", "engagement"),
    ("sessionLog", "session"),
)

# Built-in fieldName renames, keyed by the ALREADY-renamed entityType.
_RENAMED_FIELDS = (
    ("crmCompanyRef", "crmClientRefID", "crmCompanyRefID"),
    ("crmCompanyRef", "crmClientID", "crmCompanyID"),
    ("engagement", "crmEngagementRefID", "engagementID"),
    ("session", "sessionLogID", "sessionID"),
    ("session", "crmEngagementRefID", "engagementID"),
    ("session", "sessionLogDate", "scheduledAt"),
    ("session", "sessionLogSummary", "sessionNotes"),
)

_RETIRED_ENTITY_TYPES = ("meetingNote", "nextStep")

_REGISTRY = sa.table(
    "schemaRegistry",
    sa.column("entityType", sa.String),
    sa.column("fieldName", sa.String),
    sa.column("fieldType", sa.String),
    sa.column("requiredFlag", sa.Boolean),
    sa.column("userDefinedFlag", sa.Boolean),
    sa.column("deletedAt", sa.DateTime(timezone=True)),
)


def _structural_columns() -> list[sa.Column]:
    return [
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdBy", sa.Uuid(), nullable=True),
        sa.Column("modifiedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modifiedBy", sa.Uuid(), nullable=True),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletedBy", sa.Uuid(), nullable=True),
        sa.Column("rowVersion", sa.Integer(), nullable=False),
        sa.Column("customAttributes", _JSON_OBJECT, nullable=False),
    ]


def _modified_at_index(table_name: str) -> None:
    with op.batch_alter_table(table_name, schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f(f"ix_{table_name}_modifiedAt"), ["modifiedAt"], unique=False
        )


def _live_index(
    table_name: str, index_name: str, columns: list[str], *, unique: bool = False
) -> None:
    with op.batch_alter_table(table_name, schema=None) as batch_op:
        batch_op.create_index(
            index_name, columns, unique=unique, sqlite_where=_LIVE, postgresql_where=_LIVE
        )


def _copy(target: str, target_columns: str, source_select: str) -> None:
    op.execute(sa.text(f'INSERT INTO "{target}" ({target_columns}) {source_select}'))


def upgrade() -> None:
    # --- crmClientRef → crmCompanyRef (REQ-086: THE organization anchor) ---
    op.create_table(
        "crmCompanyRef",
        sa.Column("crmCompanyRefID", sa.Uuid(), nullable=False),
        sa.Column("crmCompanyID", sa.String(length=64), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("crmCompanyRefID", name=op.f("pk_crmCompanyRef")),
    )
    _modified_at_index("crmCompanyRef")
    _live_index(
        "crmCompanyRef",
        "uq_crmCompanyRef_crmCompanyID_live",
        ["crmCompanyID"],
        unique=True,
    )
    _copy(
        "crmCompanyRef",
        f'"crmCompanyRefID", "crmCompanyID", {_STRUCTURAL_COLUMNS_SQL}',
        f'SELECT "crmClientRefID", "crmClientID", {_STRUCTURAL_COLUMNS_SQL}'
        ' FROM "crmClientRef"',
    )
    op.drop_table("crmClientRef")

    # --- crmMentorRef gains the userID pairing (WTK-167/REQ-019) ---------
    # Rebuilt rather than altered: SQLite cannot add a foreign-keyed column
    # in place, and a rebuild keeps constraint names deterministic on both
    # dialects. Nothing references crmMentorRef at this point (engagement is
    # created below), so the swap is dependency-free.
    op.create_table(
        "crmMentorRefRebuild",
        sa.Column("crmMentorRefID", sa.Uuid(), nullable=False),
        sa.Column("crmMentorID", sa.String(length=64), nullable=False),
        sa.Column("userID", sa.Uuid(), nullable=True),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("crmMentorRefID", name=op.f("pk_crmMentorRef")),
        sa.ForeignKeyConstraint(
            ["userID"], ["appUser.userID"], name=op.f("fk_crmMentorRef_userID_appUser")
        ),
    )
    _copy(
        "crmMentorRefRebuild",
        f'"crmMentorRefID", "crmMentorID", {_STRUCTURAL_COLUMNS_SQL}',
        f'SELECT "crmMentorRefID", "crmMentorID", {_STRUCTURAL_COLUMNS_SQL}'
        ' FROM "crmMentorRef"',
    )
    op.drop_table("crmMentorRef")
    op.rename_table("crmMentorRefRebuild", "crmMentorRef")
    _modified_at_index("crmMentorRef")
    _live_index(
        "crmMentorRef", "uq_crmMentorRef_crmMentorID_live", ["crmMentorID"], unique=True
    )
    _live_index("crmMentorRef", "uq_crmMentorRef_userID_live", ["userID"], unique=True)

    # --- client / partner: the REQ-086 role subclasses -------------------
    op.create_table(
        "client",
        sa.Column("clientID", sa.Uuid(), nullable=False),
        sa.Column("crmCompanyRefID", sa.Uuid(), nullable=False),
        sa.Column("clientSince", sa.Date(), nullable=True),
        sa.Column("clientProgram", sa.String(length=200), nullable=True),
        sa.Column("clientReferralSource", sa.String(length=200), nullable=True),
        sa.Column("clientStage", sa.String(length=200), nullable=True),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("clientID", name=op.f("pk_client")),
        sa.ForeignKeyConstraint(
            ["crmCompanyRefID"],
            ["crmCompanyRef.crmCompanyRefID"],
            name=op.f("fk_client_crmCompanyRefID_crmCompanyRef"),
        ),
    )
    _modified_at_index("client")
    _live_index("client", "uq_client_crmCompanyRefID_live", ["crmCompanyRefID"], unique=True)

    op.create_table(
        "partner",
        sa.Column("partnerID", sa.Uuid(), nullable=False),
        sa.Column("crmCompanyRefID", sa.Uuid(), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("partnerID", name=op.f("pk_partner")),
        sa.ForeignKeyConstraint(
            ["crmCompanyRefID"],
            ["crmCompanyRef.crmCompanyRefID"],
            name=op.f("fk_partner_crmCompanyRefID_crmCompanyRef"),
        ),
    )
    _modified_at_index("partner")
    _live_index("partner", "uq_partner_crmCompanyRefID_live", ["crmCompanyRefID"], unique=True)

    # --- crmEngagementRef → engagement (the app-owned working home) ------
    op.create_table(
        "engagement",
        sa.Column("engagementID", sa.Uuid(), nullable=False),
        sa.Column("crmEngagementID", sa.String(length=64), nullable=True),
        sa.Column("engagementName", sa.String(length=200), nullable=True),
        sa.Column("engagementStatus", sa.Uuid(), nullable=True),
        sa.Column("clientID", sa.Uuid(), nullable=True),
        sa.Column("crmMentorRefID", sa.Uuid(), nullable=True),
        sa.Column("engagementSummary", sa.String(length=4000), nullable=True),
        sa.Column("primaryContactName", sa.String(length=200), nullable=True),
        sa.Column("primaryContactEmail", sa.String(length=320), nullable=True),
        sa.Column("primaryContactCrmID", sa.String(length=64), nullable=True),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("engagementID", name=op.f("pk_engagement")),
        sa.ForeignKeyConstraint(
            ["clientID"], ["client.clientID"], name=op.f("fk_engagement_clientID_client")
        ),
        sa.ForeignKeyConstraint(
            ["crmMentorRefID"],
            ["crmMentorRef.crmMentorRefID"],
            name=op.f("fk_engagement_crmMentorRefID_crmMentorRef"),
        ),
    )
    _modified_at_index("engagement")
    _live_index(
        "engagement",
        "uq_engagement_crmEngagementID_live",
        ["crmEngagementID"],
        unique=True,
    )
    _live_index("engagement", "ix_engagement_clientID_live", ["clientID"])
    _live_index("engagement", "ix_engagement_crmMentorRefID_live", ["crmMentorRefID"])
    # Carried-over anchor rows have no working data yet; the new columns are
    # API-required (registry requiredFlag) but database-nullable for exactly
    # this reason — staff complete them.
    _copy(
        "engagement",
        f'"engagementID", "crmEngagementID", {_STRUCTURAL_COLUMNS_SQL}',
        f'SELECT "crmEngagementRefID", "crmEngagementID", {_STRUCTURAL_COLUMNS_SQL}'
        ' FROM "crmEngagementRef"',
    )

    # --- sessionLog → session (REQ-074/079/082, WTK-182) -----------------
    op.create_table(
        "session",
        sa.Column("sessionID", sa.Uuid(), nullable=False),
        sa.Column("engagementID", sa.Uuid(), nullable=False),
        sa.Column("scheduledAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sessionStatus", sa.Uuid(), nullable=True),
        sa.Column("conferenceLink", sa.String(length=2000), nullable=True),
        sa.Column("sessionNotes", sa.String(length=4000), nullable=True),
        sa.Column("actionItems", sa.String(length=4000), nullable=True),
        sa.Column("transcriptText", sa.Text(), nullable=True),
        sa.Column("transcriptSource", sa.String(length=200), nullable=True),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("sessionID", name=op.f("pk_session")),
        sa.ForeignKeyConstraint(
            ["engagementID"],
            ["engagement.engagementID"],
            name=op.f("fk_session_engagementID_engagement"),
        ),
    )
    _modified_at_index("session")
    _live_index(
        "session",
        "ix_session_engagement_scheduledAt_live",
        ["engagementID", "scheduledAt"],
    )
    _copy(
        "session",
        f'"sessionID", "engagementID", "scheduledAt", "sessionNotes",'
        f" {_STRUCTURAL_COLUMNS_SQL}",
        f'SELECT "sessionLogID", "crmEngagementRefID", "sessionLogDate",'
        f' "sessionLogSummary", {_STRUCTURAL_COLUMNS_SQL} FROM "sessionLog"',
    )

    # Children before parents; meetingNote/nextStep fold onto the session's
    # rich-text fields (REQ-074/REQ-082 — see the module docstring).
    op.drop_table("sessionLog")
    op.drop_table("crmEngagementRef")
    op.drop_table("meetingNote")
    op.drop_table("nextStep")

    # --- resource (REQ-084) / event (REQ-085) ----------------------------
    op.create_table(
        "resource",
        sa.Column("resourceID", sa.Uuid(), nullable=False),
        sa.Column("resourceTitle", sa.String(length=200), nullable=False),
        sa.Column("resourceKind", sa.Uuid(), nullable=True),
        sa.Column("resourceLocation", sa.String(length=2000), nullable=False),
        sa.Column("resourceDescription", sa.String(length=2000), nullable=True),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("resourceID", name=op.f("pk_resource")),
    )
    _modified_at_index("resource")

    op.create_table(
        "event",
        sa.Column("eventID", sa.Uuid(), nullable=False),
        sa.Column("eventTitle", sa.String(length=200), nullable=False),
        sa.Column("startsAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eventLocation", sa.String(length=200), nullable=True),
        sa.Column("eventAudience", sa.String(length=200), nullable=True),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("eventID", name=op.f("pk_event")),
    )
    _modified_at_index("event")
    _live_index("event", "ix_event_startsAt_live", ["startsAt"])

    # --- registry fixups, then the seed (REQ-050) ------------------------
    for old_type, new_type in _RENAMED_ENTITY_TYPES:
        op.execute(
            _REGISTRY.update()
            .where(_REGISTRY.c.entityType == old_type)
            .values(entityType=new_type)
        )
    for entity_type, old_name, new_name in _RENAMED_FIELDS:
        op.execute(
            _REGISTRY.update()
            .where(
                _REGISTRY.c.entityType == entity_type,
                _REGISTRY.c.fieldName == old_name,
                _REGISTRY.c.userDefinedFlag.is_(False),
            )
            .values(fieldName=new_name)
        )
    # Soft-retire, never delete: the rows remain the record that these
    # fields once existed (their tables are gone above).
    op.execute(
        _REGISTRY.update()
        .where(
            _REGISTRY.c.entityType.in_(list(_RETIRED_ENTITY_TYPES)),
            _REGISTRY.c.deletedAt.is_(None),
        )
        .values(deletedAt=utcnow())
    )

    seed_built_in_registry(Session(bind=op.get_bind()), list(_SEEDED_ENTITIES))


def downgrade() -> None:
    # Downgrade restores the 0013 schema exactly (tables, columns, indexes);
    # registry-row metadata is restored to the fields that matter to the
    # mid-chain migrations (names, types, required flags) — label fidelity
    # is deliberately out of scope, no 0013-era consumer reads labels.
    # meetingNote / nextStep return as 0007 created them (empty — their
    # concepts lived on the session fields while 0014 was applied).
    op.create_table(
        "meetingNote",
        sa.Column("meetingNoteID", sa.Uuid(), nullable=False),
        sa.Column("meetingNoteBody", sa.String(length=4000), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("meetingNoteID", name=op.f("pk_meetingNote")),
    )
    _modified_at_index("meetingNote")
    op.create_table(
        "nextStep",
        sa.Column("nextStepID", sa.Uuid(), nullable=False),
        sa.Column("nextStepDescription", sa.String(length=2000), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("nextStepID", name=op.f("pk_nextStep")),
    )
    _modified_at_index("nextStep")

    # engagement → crmEngagementRef. App-born engagements carry no CRM id;
    # the anchor's column was NOT NULL + unique, so they get a synthetic
    # 'app:'-prefixed anchor derived from their own key — collision-free and
    # visibly not a CRM id.
    op.create_table(
        "crmEngagementRef",
        sa.Column("crmEngagementRefID", sa.Uuid(), nullable=False),
        sa.Column("crmEngagementID", sa.String(length=64), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("crmEngagementRefID", name=op.f("pk_crmEngagementRef")),
    )
    _modified_at_index("crmEngagementRef")
    _live_index(
        "crmEngagementRef",
        "uq_crmEngagementRef_crmEngagementID_live",
        ["crmEngagementID"],
        unique=True,
    )
    _copy(
        "crmEngagementRef",
        f'"crmEngagementRefID", "crmEngagementID", {_STRUCTURAL_COLUMNS_SQL}',
        'SELECT "engagementID",'
        ' COALESCE("crmEngagementID", \'app:\' || CAST("engagementID" AS VARCHAR)),'
        f' {_STRUCTURAL_COLUMNS_SQL} FROM "engagement"',
    )

    # session → sessionLog. sessionLogSummary was NOT NULL; a not-yet-held
    # session's empty notes become the empty string.
    op.create_table(
        "sessionLog",
        sa.Column("sessionLogID", sa.Uuid(), nullable=False),
        sa.Column("crmEngagementRefID", sa.Uuid(), nullable=False),
        sa.Column("sessionLogDate", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sessionLogSummary", sa.String(length=4000), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("sessionLogID", name=op.f("pk_sessionLog")),
        sa.ForeignKeyConstraint(
            ["crmEngagementRefID"],
            ["crmEngagementRef.crmEngagementRefID"],
            name=op.f("fk_sessionLog_crmEngagementRefID_crmEngagementRef"),
        ),
    )
    _modified_at_index("sessionLog")
    _live_index("sessionLog", "ix_sessionLog_crmEngagementRefID_live", ["crmEngagementRefID"])
    _copy(
        "sessionLog",
        f'"sessionLogID", "crmEngagementRefID", "sessionLogDate", "sessionLogSummary",'
        f" {_STRUCTURAL_COLUMNS_SQL}",
        f'SELECT "sessionID", "engagementID", "scheduledAt",'
        f' COALESCE("sessionNotes", \'\'), {_STRUCTURAL_COLUMNS_SQL} FROM "session"',
    )

    op.drop_table("session")
    op.drop_table("engagement")
    op.drop_table("resource")
    op.drop_table("event")

    # crmCompanyRef → crmClientRef, after its role subclasses are gone.
    op.drop_table("client")
    op.drop_table("partner")
    op.create_table(
        "crmClientRef",
        sa.Column("crmClientRefID", sa.Uuid(), nullable=False),
        sa.Column("crmClientID", sa.String(length=64), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("crmClientRefID", name=op.f("pk_crmClientRef")),
    )
    _modified_at_index("crmClientRef")
    _live_index(
        "crmClientRef", "uq_crmClientRef_crmClientID_live", ["crmClientID"], unique=True
    )
    _copy(
        "crmClientRef",
        f'"crmClientRefID", "crmClientID", {_STRUCTURAL_COLUMNS_SQL}',
        f'SELECT "crmCompanyRefID", "crmCompanyID", {_STRUCTURAL_COLUMNS_SQL}'
        ' FROM "crmCompanyRef"',
    )
    op.drop_table("crmCompanyRef")

    # crmMentorRef loses the userID pairing — reverse rebuild.
    op.create_table(
        "crmMentorRefRebuild",
        sa.Column("crmMentorRefID", sa.Uuid(), nullable=False),
        sa.Column("crmMentorID", sa.String(length=64), nullable=False),
        *_structural_columns(),
        sa.PrimaryKeyConstraint("crmMentorRefID", name=op.f("pk_crmMentorRef")),
    )
    _copy(
        "crmMentorRefRebuild",
        f'"crmMentorRefID", "crmMentorID", {_STRUCTURAL_COLUMNS_SQL}',
        f'SELECT "crmMentorRefID", "crmMentorID", {_STRUCTURAL_COLUMNS_SQL}'
        ' FROM "crmMentorRef"',
    )
    op.drop_table("crmMentorRef")
    op.rename_table("crmMentorRefRebuild", "crmMentorRef")
    _modified_at_index("crmMentorRef")
    _live_index(
        "crmMentorRef", "uq_crmMentorRef_crmMentorID_live", ["crmMentorID"], unique=True
    )

    _reverse_registry_fixups()


def _reverse_registry_fixups() -> None:
    # Remove rows for fields/entities that exist only at 0014+ (the 0007
    # downgrade-delete precedent), un-retire the folded entities' rows, and
    # reverse the renames with their pre-0014 types and required flags.
    op.execute(
        sa.delete(_REGISTRY).where(
            _REGISTRY.c.entityType.in_(["client", "partner", "resource", "event"]),
            _REGISTRY.c.userDefinedFlag.is_(False),
        )
    )
    added_fields = (
        ("engagement", "engagementName"),
        ("engagement", "engagementStatus"),
        ("engagement", "clientID"),
        ("engagement", "crmMentorRefID"),
        ("engagement", "engagementSummary"),
        ("engagement", "primaryContactName"),
        ("engagement", "primaryContactEmail"),
        ("engagement", "primaryContactCrmID"),
        ("session", "sessionStatus"),
        ("session", "conferenceLink"),
        ("session", "actionItems"),
        ("session", "transcriptText"),
        ("session", "transcriptSource"),
        ("crmMentorRef", "userID"),
    )
    for entity_type, field_name in added_fields:
        op.execute(
            sa.delete(_REGISTRY).where(
                _REGISTRY.c.entityType == entity_type,
                _REGISTRY.c.fieldName == field_name,
                _REGISTRY.c.userDefinedFlag.is_(False),
            )
        )
    op.execute(
        _REGISTRY.update()
        .where(_REGISTRY.c.entityType.in_(list(_RETIRED_ENTITY_TYPES)))
        .values(deletedAt=None)
    )
    for entity_type, old_name, new_name in reversed(_RENAMED_FIELDS):
        op.execute(
            _REGISTRY.update()
            .where(
                _REGISTRY.c.entityType == entity_type,
                _REGISTRY.c.fieldName == new_name,
                _REGISTRY.c.userDefinedFlag.is_(False),
            )
            .values(fieldName=old_name)
        )
    for old_type, new_type in reversed(_RENAMED_ENTITY_TYPES):
        op.execute(
            _REGISTRY.update()
            .where(_REGISTRY.c.entityType == new_type)
            .values(entityType=old_type)
        )
    # The two flags the 0014 seed changed on carried-over rows: the anchor id
    # was required pre-0014, and the session summary column was required.
    op.execute(
        _REGISTRY.update()
        .where(
            _REGISTRY.c.entityType == "crmEngagementRef",
            _REGISTRY.c.fieldName == "crmEngagementID",
        )
        .values(requiredFlag=True)
    )
    op.execute(
        _REGISTRY.update()
        .where(
            _REGISTRY.c.entityType == "sessionLog",
            _REGISTRY.c.fieldName == "sessionLogSummary",
        )
        .values(requiredFlag=True)
    )
