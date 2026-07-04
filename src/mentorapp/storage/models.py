"""Every mentorapp table: schema registry, option lists, job queue, change feed.

``schemaRegistry`` (REQ-050, REQ-051) holds one row per field of every entity —
built-in and user-defined — and is the single contract that drives UI
rendering, API validation, duplicate detection, history flags, exports, and
view columns (DB-S6). ``optionSet``/``optionValue`` (REQ-055) hold
choice-field options as data, never as database enums or CHECK constraints
(DB-S7). ``backgroundJob`` (REQ-058, REQ-014) is the one queue behind all
background work (DB-S11). ``changeFeedEntry`` (REQ-057) is the append-only
ledger behind ``GET /changes?since=<watermark>`` (DB-S10).

Associations: ``optionValue.optionSetID`` → each value belongs to one set;
``schemaRegistry.optionSetID`` → a choice field's registry row points at the
set it draws from (sets are shareable across fields).
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
    option_set_id: Mapped[uuid.UUID] = mapped_column(
        "optionSetID", ForeignKey("optionSet.optionSetID"), nullable=False, index=True
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

    The partial unique index on ``fieldName`` alone is the mechanical enforcement
    of DB-R2's system-wide field-name uniqueness: one registry table, one name.
    ``fieldType`` and ``validationRules`` are typed/validated by the API layer
    against this registry — the database holds no enum of types (DB-S7).
    """

    __tablename__ = "schemaRegistry"
    __table_args__ = (
        Index(
            "uq_schemaRegistry_fieldName_live",
            "fieldName",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
        # GET /schema/{entity} reads the whole registry for one entity.
        Index("ix_schemaRegistry_entityType_live", "entityType", sqlite_where=_LIVE),
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
    # Null for non-choice fields; choice fields (built-in or custom) point at their set.
    option_set_id: Mapped[uuid.UUID | None] = mapped_column(
        "optionSetID", ForeignKey("optionSet.optionSetID"), default=None, index=True
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
