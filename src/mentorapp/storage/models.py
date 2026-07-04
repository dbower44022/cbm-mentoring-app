"""The schema registry and option-list tables (REQ-050, REQ-051, REQ-055).

``schemaRegistry`` holds one row per field of every entity — built-in and
user-defined — and is the single contract that drives UI rendering, API
validation, duplicate detection, history flags, exports, and view columns
(DB-S6). ``optionSet``/``optionValue`` hold choice-field options as data,
never as database enums or CHECK constraints (DB-S7).

Associations: ``optionValue.optionSetID`` → each value belongs to one set;
``schemaRegistry.optionSetID`` → a choice field's registry row points at the
set it draws from (sets are shareable across fields).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mentorapp.storage.base import Base, JsonValue, StructuralColumnsMixin, uuid7

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
