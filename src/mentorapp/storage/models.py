"""Platform tables: registries, options, jobs, feed, and supporting entities.

Auth and access tables (users, sessions, tokens, grants, data sources) live
in ``mentorapp.storage.auth`` (WTK-001).

``schemaRegistry`` (REQ-050, REQ-051) holds one row per field of every entity —
built-in and user-defined — and is the single contract that drives UI
rendering, API validation, duplicate detection, history flags, exports, and
view columns (DB-S6). ``optionSet``/``optionValue`` (REQ-055) hold
choice-field options as data, never as database enums or CHECK constraints
(DB-S7). ``backgroundJob`` (REQ-058, REQ-014) is the one queue behind all
background work (DB-S11). ``notification`` (REQ-014) is the per-user bell
entry a job's terminal transition produces. ``changeFeedEntry`` (REQ-057) is
the append-only ledger behind ``GET /changes?since=<watermark>`` (DB-S10).

Supporting entities (WTK-128): ``fieldChange`` (REQ-054) is the one
system-wide history table for history-tracked fields; ``duplicateOverride``
(REQ-059) records every explicit duplicate-detection override;
``userPreference`` (REQ-060) is the single store for all per-user
personalization with org-default rows; ``postalCode`` (REQ-061) is the
refreshable postal → city/state reference table. The workprocess entities
(REQ-041/REQ-042) live in ``mentorapp.storage.workprocess`` (WTK-090).
REQ-049's storage surface (the attribute registry + per-table
``customAttributes``) already ships in ``schemaRegistry`` and the structural
columns — nothing is duplicated here.

Associations: ``optionValue.optionSetID`` → each value belongs to one set;
``schemaRegistry.optionSetID`` → a choice field's registry row points at the
set it draws from (sets are shareable across fields);
``notification.jobID`` → a bell entry points at the job whose terminal
transition produced it (the notification ↔ backgroundJob association), and
``notification.userID`` → the ``appUser`` it addresses.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mentorapp.storage.base import Base, JsonValue, StructuralColumnsMixin, utcnow, uuid7

# All unique constraints and lookup indexes are partial (WHERE deletedAt IS NULL,
# DB-S3): live reads never pay for soft-deleted rows, and re-creating a live row
# that duplicates a deleted corpse never collides.
_LIVE = text('"deletedAt" IS NULL')


class OptionSet(StructuralColumnsMixin, Base):
    """A named, shareable list of choice-field options (DB-S7)."""

    __tablename__ = "optionSet"
    __table_args__ = (
        Index(
            "uq_optionSet_optionSetName_live",
            "optionSetName",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    option_set_id: Mapped[uuid.UUID] = mapped_column(
        "optionSetID", primary_key=True, default=uuid7
    )
    option_set_name: Mapped[str] = mapped_column("optionSetName", String(200), nullable=False)

    option_values: Mapped[list[OptionValue]] = relationship(
        back_populates="option_set", order_by="OptionValue.option_value_sort_order"
    )


class OptionValue(StructuralColumnsMixin, Base):
    """One selectable value within an option set; records store its ID (DB-S7).

    Retiring a value is ``activeFlag`` off — hidden from new entry while
    historical records still render. Renaming ``optionValueLabel`` is one row
    update and zero record touches.
    """

    __tablename__ = "optionValue"
    __table_args__ = (
        Index(
            "uq_optionValue_set_name_live",
            "optionSetID",
            "optionValueName",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    option_value_id: Mapped[uuid.UUID] = mapped_column(
        "optionValueID", primary_key=True, default=uuid7
    )
    # The by-set lookup rides the partial unique index above (optionSetID is
    # its leading column) — a separate FK index would be dead weight and
    # would break the REQ-052 partial-index rule.
    option_set_id: Mapped[uuid.UUID] = mapped_column(
        "optionSetID", ForeignKey("optionSet.optionSetID"), nullable=False
    )
    option_value_name: Mapped[str] = mapped_column(
        "optionValueName", String(200), nullable=False
    )
    option_value_label: Mapped[str] = mapped_column(
        "optionValueLabel", String(200), nullable=False
    )
    option_value_sort_order: Mapped[int] = mapped_column(
        "optionValueSortOrder", nullable=False, default=0
    )
    active_flag: Mapped[bool] = mapped_column("activeFlag", nullable=False, default=True)

    option_set: Mapped[OptionSet] = relationship(back_populates="option_values")


class SchemaRegistry(StructuralColumnsMixin, Base):
    """Per-field metadata row — the schema-of-record for every entity field (DB-S6).

    One row per (entity, field): DB-R2b lets a foreign key re-appear on other
    entities under the identical name as the PK it references (sessionLog's
    ``crmEngagementRefID``), and each appearance needs its own row so views,
    ``GET /schema/{entity}``, and write validation see the field on every
    entity that carries it. System-wide fieldName uniqueness for everything
    *but* that R2b shape is enforced where rows are created — the registry
    seed for built-ins (``registry_seed``) — since no index can express the
    exception. ``fieldType`` and ``validationRules`` are typed/validated by
    the API layer against this registry — the database holds no enum of
    types (DB-S7).

    The row IS the field setting (REQ-033, REQ-040): ``requiredFlag``,
    ``validationRules``, ``defaultValue``, and ``helpText`` are the single
    authority every form applies — never per-form hand rules.
    """

    __tablename__ = "schemaRegistry"
    __table_args__ = (
        # Also serves the GET /schema/{entity} scan via its leading column —
        # a separate entityType index would be dead weight.
        Index(
            "uq_schemaRegistry_entity_fieldName_live",
            "entityType",
            "fieldName",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
        # The admin-UI read "which fields use this option set" (DB-S7);
        # partial per the REQ-052 rule.
        Index(
            "ix_schemaRegistry_optionSetID_live",
            "optionSetID",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    schema_registry_id: Mapped[uuid.UUID] = mapped_column(
        "schemaRegistryID", primary_key=True, default=uuid7
    )
    # Same vocabulary as the fieldChange history table (DB-S5): entityType + fieldName
    # mean the same thing wherever they appear (DB-R2).
    entity_type: Mapped[str] = mapped_column("entityType", String(100), nullable=False)
    field_name: Mapped[str] = mapped_column("fieldName", String(100), nullable=False)
    field_type: Mapped[str] = mapped_column("fieldType", String(50), nullable=False)
    field_label: Mapped[str] = mapped_column("fieldLabel", String(200), nullable=False)
    required_flag: Mapped[bool] = mapped_column("requiredFlag", nullable=False, default=False)
    validation_rules: Mapped[dict[str, Any] | None] = mapped_column(
        "validationRules", JsonValue, default=None
    )
    # What pre-populates the field on a new record (REQ-033). JSON so every
    # fieldType's default is representable; typed against fieldType by the
    # API layer, exactly like validationRules. No mapped default on these two
    # (unlike their siblings): an explicit default puts the column in every
    # ORM INSERT, and the registry seed must be able to run mid-migration-
    # chain against a schemaRegistry table that predates 0008.
    default_value: Mapped[Any | None] = mapped_column("defaultValue", JsonValue)
    # Admin-maintained field-level help, surfaced on hover/focus (REQ-040) —
    # content lives here, never hardcoded in a form.
    help_text: Mapped[str | None] = mapped_column("helpText", String(2000))
    # Null for non-choice fields; choice fields (built-in or custom) point at their set.
    option_set_id: Mapped[uuid.UUID | None] = mapped_column(
        "optionSetID", ForeignKey("optionSet.optionSetID"), default=None
    )
    history_tracked_flag: Mapped[bool] = mapped_column(
        "historyTrackedFlag", nullable=False, default=False
    )
    # Declares the trigram-searchable column set per entity (DB-S8/REQ-055, opt-in).
    searchable_flag: Mapped[bool] = mapped_column(
        "searchableFlag", nullable=False, default=False
    )
    visibility_hints: Mapped[dict[str, Any] | None] = mapped_column(
        "visibilityHints", JsonValue, default=None
    )
    # False = built-in (seeded by migration, drift-checked at startup);
    # True = admin-created custom attribute living in customAttributes JSONB (DB-R3).
    user_defined_flag: Mapped[bool] = mapped_column(
        "userDefinedFlag", nullable=False, default=False
    )

    option_set: Mapped[OptionSet | None] = relationship()


# DB-S11's status lifecycle. Validated by the app layer against this vocabulary —
# never a database enum or CHECK constraint (DB-S7).
JOB_STATUSES: Final[tuple[str, ...]] = (
    "pending",
    "processing",
    "completed",
    "failed",
    "needsAttention",
)

# DB-S10: soft deletes and restores are first-class feed events, not bare updates,
# so client caches can drop a record they can no longer fetch.
CHANGE_KINDS: Final[tuple[str, ...]] = ("created", "updated", "deleted", "restored")


class BackgroundJob(StructuralColumnsMixin, Base):
    """One queued unit of background work — the single queue for all job types (DB-S11).

    Lifecycle (``JOB_STATUSES``): pending → processing → completed | failed |
    needsAttention. Workers claim due rows (``jobStatus`` pending and
    ``runAfter`` <= now) with ``FOR UPDATE SKIP LOCKED`` and take a lease by
    setting ``lockedUntil``; a processing row whose lease has expired is
    claimable again, so a crashed worker's job is reclaimed without operator
    action. Transient failures increment ``attemptCount`` and push ``runAfter``
    out with backoff to a cap; permanent failures park as ``needsAttention``.
    """

    __tablename__ = "backgroundJob"
    __table_args__ = (
        # The worker's claim scan: due pending work.
        Index(
            "ix_backgroundJob_claim_live",
            "jobStatus",
            "runAfter",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
        # The crash-reclaim scan: processing rows whose lease has expired.
        Index(
            "ix_backgroundJob_lease_live",
            "jobStatus",
            "lockedUntil",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
        # The retention-trim scan; partial on the expiring minority of rows.
        Index(
            "ix_backgroundJob_expiry",
            "jobExpiresAt",
            sqlite_where=text('"jobExpiresAt" IS NOT NULL'),
            postgresql_where=text('"jobExpiresAt" IS NOT NULL'),
        ),
    )

    # DB-S11 fixes this key's public name (``GET /jobs/{jobID}``); it keeps one
    # meaning system-wide (DB-R2) — there is no other job table.
    job_id: Mapped[uuid.UUID] = mapped_column("jobID", primary_key=True, default=uuid7)
    job_type: Mapped[str] = mapped_column("jobType", String(100), nullable=False)
    job_payload: Mapped[dict[str, Any]] = mapped_column(
        "jobPayload", JsonValue, nullable=False, default=dict
    )
    job_status: Mapped[str] = mapped_column(
        "jobStatus", String(50), nullable=False, default="pending"
    )
    attempt_count: Mapped[int] = mapped_column("attemptCount", nullable=False, default=0)
    # A job is due when runAfter <= now; retry backoff reschedules by moving it out.
    run_after: Mapped[datetime] = mapped_column(
        "runAfter", DateTime(timezone=True), nullable=False, default=utcnow
    )
    # The worker's lease. Null on unclaimed rows; an expired lease means the
    # claiming worker died and the row is claimable again.
    locked_until: Mapped[datetime | None] = mapped_column(
        "lockedUntil", DateTime(timezone=True), default=None
    )
    # When the retention job may trim this row (and its artifact). Null = keep.
    job_expires_at: Mapped[datetime | None] = mapped_column(
        "jobExpiresAt", DateTime(timezone=True), default=None
    )
    # Download link written on completion of export/print job types (DB-S11):
    # big result sets travel as artifacts, never in API responses.
    artifact_url: Mapped[str | None] = mapped_column("artifactUrl", String(2000), default=None)
    # Handler-written progress document behind GET /jobs/{jobID} and the status
    # bar (REQ-014); shape is per job type (e.g. {"current": 3, "total": 10}).
    # Progress is polled, never a feed event — only terminal transitions reach
    # the change feed (DB-S10 keeps sync traffic proportional to what changed).
    job_progress: Mapped[dict[str, Any] | None] = mapped_column(
        "jobProgress", JsonValue, default=None
    )


# REQ-014's bell vocabulary — app-validated, never a database enum (DB-S7).
# Both terminal failure statuses (failed, needsAttention) surface to the user
# as jobFailed: needsAttention is an operator distinction, not a mentor-facing
# one, and failure entries speak the educate voice either way.
NOTIFICATION_TYPES: Final[tuple[str, ...]] = ("jobCompleted", "jobFailed")

# The badge count scans only unread live rows; the trim scan only expiring ones.
_LIVE_UNREAD = text('"deletedAt" IS NULL AND "readAt" IS NULL')
_LIVE_JOB_LINKED = text('"deletedAt" IS NULL AND "jobID" IS NOT NULL')


class Notification(StructuralColumnsMixin, Base):
    """One per-user bell entry — a background task's completion or failure (REQ-014).

    Written in the same transaction as the job's terminal transition and
    addressed to the job's requesting user (``createdBy`` on the job row), so
    the bell and the job row can never disagree. ``readAt`` null = unread (the
    badge count); viewing the bell stamps it. Entries expire over time
    (``notificationExpiresAt``) and are trimmed by a retention job type,
    exactly like job rows. ``jobID`` is nullable so a future non-job bell
    entry needs no schema change.
    """

    __tablename__ = "notification"
    __table_args__ = (
        # The bell list read: one user's live entries, newest first.
        Index(
            "ix_notification_bell_live",
            "userID",
            "createdAt",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
        # The unread badge count — an index-only scan over the unread minority.
        Index(
            "ix_notification_unread",
            "userID",
            sqlite_where=_LIVE_UNREAD,
            postgresql_where=_LIVE_UNREAD,
        ),
        # At most one live entry per (job, user): a crash-reclaimed worker
        # re-running a terminal transition can never double-notify (the same
        # at-least-once absorption the change feed relies on).
        Index(
            "uq_notification_job_user_live",
            "jobID",
            "userID",
            unique=True,
            sqlite_where=_LIVE_JOB_LINKED,
            postgresql_where=_LIVE_JOB_LINKED,
        ),
        # The retention-trim scan; same shape as ix_backgroundJob_expiry.
        Index(
            "ix_notification_expiry",
            "notificationExpiresAt",
            sqlite_where=text('"notificationExpiresAt" IS NOT NULL'),
            postgresql_where=text('"notificationExpiresAt" IS NOT NULL'),
        ),
    )

    notification_id: Mapped[uuid.UUID] = mapped_column(
        "notificationID", primary_key=True, default=uuid7
    )
    # A real FK (unlike userPreference.userID): the bell only ever addresses
    # authenticated app users, so an orphan notification is a defect.
    user_id: Mapped[uuid.UUID] = mapped_column(
        "userID", ForeignKey("appUser.userID"), nullable=False
    )
    notification_type: Mapped[str] = mapped_column(
        "notificationType", String(50), nullable=False
    )
    # The rendered bell text, composed at write time in the educate voice —
    # the bell never re-derives wording from job state.
    notification_message: Mapped[str] = mapped_column(
        "notificationMessage", String(2000), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        "jobID", ForeignKey("backgroundJob.jobID"), default=None
    )
    # Null = unread. Stamped when the user views the bell, per REQ-014.
    read_at: Mapped[datetime | None] = mapped_column(
        "readAt", DateTime(timezone=True), default=None
    )
    # When the retention job may trim this row. Null = keep.
    notification_expires_at: Mapped[datetime | None] = mapped_column(
        "notificationExpiresAt", DateTime(timezone=True), default=None
    )


class ChangeFeedEntry(StructuralColumnsMixin, Base):
    """One record-change event behind ``GET /changes?since=<watermark>`` (DB-S10).

    Append-only ledger: an entry is written in the same transaction as the
    record write it describes and is never modified afterward. The watermark is
    the keyset cursor ``(changedAt, changeFeedEntryID)`` — the same
    sort-value-plus-ID-tiebreak shape as list pagination (DB-S8) — monotonic
    for sequential writes and safe under ties because catch-up from any older
    watermark replays at-least-once, which the feed's idempotent contract
    absorbs. Retention trimming of old entries is allowed without schema change.
    """

    __tablename__ = "changeFeedEntry"
    __table_args__ = (
        # The monotonic watermark index: every /changes read is one forward scan
        # from the caller's cursor.
        Index(
            "ix_changeFeedEntry_watermark_live",
            "changedAt",
            "changeFeedEntryID",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    change_feed_entry_id: Mapped[uuid.UUID] = mapped_column(
        "changeFeedEntryID", primary_key=True, default=uuid7
    )
    # Same vocabulary as schemaRegistry.entityType and the fieldChange table (DB-R2).
    entity_type: Mapped[str] = mapped_column("entityType", String(100), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column("recordID", nullable=False)
    # The DB-S10 tuple's rowVersion — the changed record's version at event time.
    # Named recordRowVersion because rowVersion is the structural column of this
    # entry row itself (DB-R2: one name, one meaning).
    record_row_version: Mapped[int] = mapped_column("recordRowVersion", nullable=False)
    change_kind: Mapped[str] = mapped_column("changeKind", String(50), nullable=False)
    # When the described change happened — same vocabulary as fieldChange (DB-S5).
    changed_at: Mapped[datetime] = mapped_column(
        "changedAt", DateTime(timezone=True), nullable=False, default=utcnow
    )


class FieldChange(StructuralColumnsMixin, Base):
    """One old → new transition of a history-tracked field (DB-S5, REQ-054).

    Written by the API whenever a field whose registry row sets
    ``historyTrackedFlag`` changes; untracked fields never appear here. A
    record's History panel is one indexed lookup on ``(entityType, recordID)``,
    already ordered. Display-grade audit trail — not a backup, not event
    sourcing; retention trimming is allowed later without schema change.
    """

    __tablename__ = "fieldChange"
    __table_args__ = (
        # The History panel read: every tracked change for one record, in order.
        Index(
            "ix_fieldChange_record_live",
            "entityType",
            "recordID",
            "changedAt",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    field_change_id: Mapped[uuid.UUID] = mapped_column(
        "fieldChangeID", primary_key=True, default=uuid7
    )
    # Same vocabulary as schemaRegistry.entityType/fieldName and
    # changeFeedEntry.recordID/changedAt (DB-R2: one name, one meaning).
    entity_type: Mapped[str] = mapped_column("entityType", String(100), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column("recordID", nullable=False)
    field_name: Mapped[str] = mapped_column("fieldName", String(100), nullable=False)
    # JSON rather than text so typed values (numbers, lists, option IDs) survive
    # round-trip; null is a legitimate value on either side (field set/cleared).
    old_value: Mapped[Any | None] = mapped_column("oldValue", JsonValue, default=None)
    new_value: Mapped[Any | None] = mapped_column("newValue", JsonValue, default=None)
    changed_at: Mapped[datetime] = mapped_column(
        "changedAt", DateTime(timezone=True), nullable=False, default=utcnow
    )
    changed_by: Mapped[uuid.UUID | None] = mapped_column("changedBy", default=None)


class DuplicateOverride(StructuralColumnsMixin, Base):
    """The recorded explicit override of a duplicate-detection rejection (DB-S12, REQ-059).

    A create matching the entity's registry-declared match rules is rejected
    with candidates; resubmitting with the override flag creates the record AND
    writes this row in the same transaction — the override is history, never a
    silent bypass. ``candidateRecordIDs``/``matchedRuleNames`` snapshot what the
    user saw and dismissed (candidates may later merge or soft-delete), so they
    are stored values, not foreign keys.
    """

    __tablename__ = "duplicateOverride"
    __table_args__ = (
        # The audit read: overrides recorded against one created record.
        Index(
            "ix_duplicateOverride_record_live",
            "entityType",
            "recordID",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    duplicate_override_id: Mapped[uuid.UUID] = mapped_column(
        "duplicateOverrideID", primary_key=True, default=uuid7
    )
    # The record created despite the match — same recordID vocabulary (DB-R2).
    entity_type: Mapped[str] = mapped_column("entityType", String(100), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column("recordID", nullable=False)
    # Registry match-rule names that fired; several can match one create
    # (e.g. by-email AND by-name+phone), so this is a list, not a scalar.
    matched_rule_names: Mapped[list[str]] = mapped_column(
        "matchedRuleNames", JsonValue, nullable=False, default=list
    )
    candidate_record_ids: Mapped[list[str]] = mapped_column(
        "candidateRecordIDs", JsonValue, nullable=False, default=list
    )
    override_reason: Mapped[str | None] = mapped_column(
        "overrideReason", String(2000), default=None
    )


# Live-row predicates split by userID because unique indexes never collide NULLs
# (on either dialect): user rows and the org-default row are enforced separately.
_LIVE_USER_ROW = text('"deletedAt" IS NULL AND "userID" IS NOT NULL')
_LIVE_ORG_DEFAULT = text('"deletedAt" IS NULL AND "userID" IS NULL')


class UserPreference(StructuralColumnsMixin, Base):
    """One namespaced preference document per user — or the org default (DB-S13, REQ-060).

    All view/pin/layout/filter/startup persistence rides this one table behind
    ``GET/PUT /preferences/{key}`` — a new grid feature needs no migration.
    Null ``userID`` marks the organization-wide default row, which a user's own
    row overrides at read time. ``userID`` is a bare UUID (no FK): staff
    identities live in the CRM system of record, and the API stamps the
    session user, exactly as it does for the audit columns.
    """

    __tablename__ = "userPreference"
    __table_args__ = (
        Index(
            "uq_userPreference_user_key_live",
            "userID",
            "preferenceKey",
            unique=True,
            sqlite_where=_LIVE_USER_ROW,
            postgresql_where=_LIVE_USER_ROW,
        ),
        Index(
            "uq_userPreference_orgDefault_key_live",
            "preferenceKey",
            unique=True,
            sqlite_where=_LIVE_ORG_DEFAULT,
            postgresql_where=_LIVE_ORG_DEFAULT,
        ),
    )

    user_preference_id: Mapped[uuid.UUID] = mapped_column(
        "userPreferenceID", primary_key=True, default=uuid7
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column("userID", default=None)
    # Namespaced key, e.g. grid.mentorRoster.columns or nav.pinnedViews (DB-S13).
    preference_key: Mapped[str] = mapped_column("preferenceKey", String(200), nullable=False)
    preference_value: Mapped[dict[str, Any]] = mapped_column(
        "preferenceValue", JsonValue, nullable=False, default=dict
    )


class PostalCode(StructuralColumnsMixin, Base):
    """One postal-code → city/state lookup row (DB-S13, REQ-061).

    Reference data, not user data: loaded and refreshed as a job type on the
    one background queue — never hand-edited. The lookup feeds form auto-fill
    and the same normalizers the duplicate-detection shadow columns use, so
    address equality has exactly one definition.
    """

    __tablename__ = "postalCode"
    __table_args__ = (
        Index(
            "uq_postalCode_country_value_live",
            "countryCode",
            "postalCodeValue",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    postal_code_id: Mapped[uuid.UUID] = mapped_column(
        "postalCodeID", primary_key=True, default=uuid7
    )
    # ISO 3166-1 alpha-2. CBM operates US-only today; the column keeps the
    # refresh job honest if that changes, without a schema change.
    country_code: Mapped[str] = mapped_column(
        "countryCode", String(2), nullable=False, default="US"
    )
    postal_code_value: Mapped[str] = mapped_column(
        "postalCodeValue", String(20), nullable=False
    )
    city_name: Mapped[str] = mapped_column("cityName", String(200), nullable=False)
    state_code: Mapped[str] = mapped_column("stateCode", String(10), nullable=False)
