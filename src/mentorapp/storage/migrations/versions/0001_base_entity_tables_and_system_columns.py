"""Base-entity tables and system columns (WTK-133).

Creates every entity table with the eight structural system columns
(audit, soft delete, ``rowVersion``, ``customAttributes`` — REQ-053,
REQ-052, REQ-048) plus the ``fieldChange`` history table (REQ-054).
Primary keys are UUIDv7, generated in the app layer before insert
(REQ-047) — no database-side default, no auto-increment. All unique
constraints and lookup indexes are partial over live rows
(``WHERE "deletedAt" IS NULL``, REQ-052); the two deliberate exceptions
are documented in the models (``modifiedAt`` feeds the change feed,
``ix_backgroundJob_expiry`` is partial on its own column).

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Mirrors storage.base.JsonValue: JSONB on Postgres, plain JSON on SQLite.
_JSON_OBJECT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "backgroundJob",
        sa.Column("jobID", sa.Uuid(), nullable=False),
        sa.Column("jobType", sa.String(length=100), nullable=False),
        sa.Column("jobPayload", _JSON_OBJECT, nullable=False),
        sa.Column("jobStatus", sa.String(length=50), nullable=False),
        sa.Column("attemptCount", sa.Integer(), nullable=False),
        sa.Column("runAfter", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lockedUntil", sa.DateTime(timezone=True), nullable=True),
        sa.Column("jobExpiresAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("artifactUrl", sa.String(length=2000), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdBy", sa.Uuid(), nullable=True),
        sa.Column("modifiedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modifiedBy", sa.Uuid(), nullable=True),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletedBy", sa.Uuid(), nullable=True),
        sa.Column("rowVersion", sa.Integer(), nullable=False),
        sa.Column("customAttributes", _JSON_OBJECT, nullable=False),
        sa.PrimaryKeyConstraint("jobID", name=op.f("pk_backgroundJob")),
    )
    with op.batch_alter_table("backgroundJob", schema=None) as batch_op:
        batch_op.create_index(
            "ix_backgroundJob_claim_live",
            ["jobStatus", "runAfter"],
            unique=False,
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )
        batch_op.create_index(
            "ix_backgroundJob_expiry",
            ["jobExpiresAt"],
            unique=False,
            sqlite_where=sa.text('"jobExpiresAt" IS NOT NULL'),
            postgresql_where=sa.text('"jobExpiresAt" IS NOT NULL'),
        )
        batch_op.create_index(
            "ix_backgroundJob_lease_live",
            ["jobStatus", "lockedUntil"],
            unique=False,
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )
        batch_op.create_index(
            batch_op.f("ix_backgroundJob_modifiedAt"), ["modifiedAt"], unique=False
        )

    op.create_table(
        "changeFeedEntry",
        sa.Column("changeFeedEntryID", sa.Uuid(), nullable=False),
        sa.Column("entityType", sa.String(length=100), nullable=False),
        sa.Column("recordID", sa.Uuid(), nullable=False),
        sa.Column("recordRowVersion", sa.Integer(), nullable=False),
        sa.Column("changeKind", sa.String(length=50), nullable=False),
        sa.Column("changedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdBy", sa.Uuid(), nullable=True),
        sa.Column("modifiedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modifiedBy", sa.Uuid(), nullable=True),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletedBy", sa.Uuid(), nullable=True),
        sa.Column("rowVersion", sa.Integer(), nullable=False),
        sa.Column("customAttributes", _JSON_OBJECT, nullable=False),
        sa.PrimaryKeyConstraint("changeFeedEntryID", name=op.f("pk_changeFeedEntry")),
    )
    with op.batch_alter_table("changeFeedEntry", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_changeFeedEntry_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "ix_changeFeedEntry_watermark_live",
            ["changedAt", "changeFeedEntryID"],
            unique=False,
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )

    op.create_table(
        "duplicateOverride",
        sa.Column("duplicateOverrideID", sa.Uuid(), nullable=False),
        sa.Column("entityType", sa.String(length=100), nullable=False),
        sa.Column("recordID", sa.Uuid(), nullable=False),
        sa.Column("matchedRuleNames", _JSON_OBJECT, nullable=False),
        sa.Column("candidateRecordIDs", _JSON_OBJECT, nullable=False),
        sa.Column("overrideReason", sa.String(length=2000), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdBy", sa.Uuid(), nullable=True),
        sa.Column("modifiedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modifiedBy", sa.Uuid(), nullable=True),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletedBy", sa.Uuid(), nullable=True),
        sa.Column("rowVersion", sa.Integer(), nullable=False),
        sa.Column("customAttributes", _JSON_OBJECT, nullable=False),
        sa.PrimaryKeyConstraint("duplicateOverrideID", name=op.f("pk_duplicateOverride")),
    )
    with op.batch_alter_table("duplicateOverride", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_duplicateOverride_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "ix_duplicateOverride_record_live",
            ["entityType", "recordID"],
            unique=False,
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )

    op.create_table(
        "fieldChange",
        sa.Column("fieldChangeID", sa.Uuid(), nullable=False),
        sa.Column("entityType", sa.String(length=100), nullable=False),
        sa.Column("recordID", sa.Uuid(), nullable=False),
        sa.Column("fieldName", sa.String(length=100), nullable=False),
        sa.Column("oldValue", _JSON_OBJECT, nullable=True),
        sa.Column("newValue", _JSON_OBJECT, nullable=True),
        sa.Column("changedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changedBy", sa.Uuid(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdBy", sa.Uuid(), nullable=True),
        sa.Column("modifiedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modifiedBy", sa.Uuid(), nullable=True),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletedBy", sa.Uuid(), nullable=True),
        sa.Column("rowVersion", sa.Integer(), nullable=False),
        sa.Column("customAttributes", _JSON_OBJECT, nullable=False),
        sa.PrimaryKeyConstraint("fieldChangeID", name=op.f("pk_fieldChange")),
    )
    with op.batch_alter_table("fieldChange", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_fieldChange_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "ix_fieldChange_record_live",
            ["entityType", "recordID", "changedAt"],
            unique=False,
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )

    op.create_table(
        "optionSet",
        sa.Column("optionSetID", sa.Uuid(), nullable=False),
        sa.Column("optionSetName", sa.String(length=200), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdBy", sa.Uuid(), nullable=True),
        sa.Column("modifiedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modifiedBy", sa.Uuid(), nullable=True),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletedBy", sa.Uuid(), nullable=True),
        sa.Column("rowVersion", sa.Integer(), nullable=False),
        sa.Column("customAttributes", _JSON_OBJECT, nullable=False),
        sa.PrimaryKeyConstraint("optionSetID", name=op.f("pk_optionSet")),
    )
    with op.batch_alter_table("optionSet", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_optionSet_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_optionSet_optionSetName_live",
            ["optionSetName"],
            unique=True,
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )

    op.create_table(
        "postalCode",
        sa.Column("postalCodeID", sa.Uuid(), nullable=False),
        sa.Column("countryCode", sa.String(length=2), nullable=False),
        sa.Column("postalCodeValue", sa.String(length=20), nullable=False),
        sa.Column("cityName", sa.String(length=200), nullable=False),
        sa.Column("stateCode", sa.String(length=10), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdBy", sa.Uuid(), nullable=True),
        sa.Column("modifiedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modifiedBy", sa.Uuid(), nullable=True),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletedBy", sa.Uuid(), nullable=True),
        sa.Column("rowVersion", sa.Integer(), nullable=False),
        sa.Column("customAttributes", _JSON_OBJECT, nullable=False),
        sa.PrimaryKeyConstraint("postalCodeID", name=op.f("pk_postalCode")),
    )
    with op.batch_alter_table("postalCode", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_postalCode_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_postalCode_country_value_live",
            ["countryCode", "postalCodeValue"],
            unique=True,
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )

    op.create_table(
        "userPreference",
        sa.Column("userPreferenceID", sa.Uuid(), nullable=False),
        sa.Column("userID", sa.Uuid(), nullable=True),
        sa.Column("preferenceKey", sa.String(length=200), nullable=False),
        sa.Column("preferenceValue", _JSON_OBJECT, nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdBy", sa.Uuid(), nullable=True),
        sa.Column("modifiedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modifiedBy", sa.Uuid(), nullable=True),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletedBy", sa.Uuid(), nullable=True),
        sa.Column("rowVersion", sa.Integer(), nullable=False),
        sa.Column("customAttributes", _JSON_OBJECT, nullable=False),
        sa.PrimaryKeyConstraint("userPreferenceID", name=op.f("pk_userPreference")),
    )
    with op.batch_alter_table("userPreference", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_userPreference_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_userPreference_orgDefault_key_live",
            ["preferenceKey"],
            unique=True,
            sqlite_where=sa.text('"deletedAt" IS NULL AND "userID" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL AND "userID" IS NULL'),
        )
        batch_op.create_index(
            "uq_userPreference_user_key_live",
            ["userID", "preferenceKey"],
            unique=True,
            sqlite_where=sa.text('"deletedAt" IS NULL AND "userID" IS NOT NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL AND "userID" IS NOT NULL'),
        )

    op.create_table(
        "workprocessRegistration",
        sa.Column("workprocessRegistrationID", sa.Uuid(), nullable=False),
        sa.Column("workprocessName", sa.String(length=200), nullable=False),
        sa.Column("workprocessDescription", sa.String(length=2000), nullable=False),
        sa.Column("targetDataSourceKeys", _JSON_OBJECT, nullable=False),
        sa.Column("selectionContract", sa.String(length=50), nullable=False),
        sa.Column("actionClassification", sa.String(length=100), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdBy", sa.Uuid(), nullable=True),
        sa.Column("modifiedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modifiedBy", sa.Uuid(), nullable=True),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletedBy", sa.Uuid(), nullable=True),
        sa.Column("rowVersion", sa.Integer(), nullable=False),
        sa.Column("customAttributes", _JSON_OBJECT, nullable=False),
        sa.PrimaryKeyConstraint(
            "workprocessRegistrationID", name=op.f("pk_workprocessRegistration")
        ),
    )
    with op.batch_alter_table("workprocessRegistration", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_workprocessRegistration_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_workprocessRegistration_name_live",
            ["workprocessName"],
            unique=True,
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )

    op.create_table(
        "optionValue",
        sa.Column("optionValueID", sa.Uuid(), nullable=False),
        sa.Column("optionSetID", sa.Uuid(), nullable=False),
        sa.Column("optionValueName", sa.String(length=200), nullable=False),
        sa.Column("optionValueLabel", sa.String(length=200), nullable=False),
        sa.Column("optionValueSortOrder", sa.Integer(), nullable=False),
        sa.Column("activeFlag", sa.Boolean(), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdBy", sa.Uuid(), nullable=True),
        sa.Column("modifiedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modifiedBy", sa.Uuid(), nullable=True),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletedBy", sa.Uuid(), nullable=True),
        sa.Column("rowVersion", sa.Integer(), nullable=False),
        sa.Column("customAttributes", _JSON_OBJECT, nullable=False),
        sa.ForeignKeyConstraint(
            ["optionSetID"],
            ["optionSet.optionSetID"],
            name=op.f("fk_optionValue_optionSetID_optionSet"),
        ),
        sa.PrimaryKeyConstraint("optionValueID", name=op.f("pk_optionValue")),
    )
    with op.batch_alter_table("optionValue", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_optionValue_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "uq_optionValue_set_name_live",
            ["optionSetID", "optionValueName"],
            unique=True,
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )

    op.create_table(
        "schemaRegistry",
        sa.Column("schemaRegistryID", sa.Uuid(), nullable=False),
        sa.Column("entityType", sa.String(length=100), nullable=False),
        sa.Column("fieldName", sa.String(length=100), nullable=False),
        sa.Column("fieldType", sa.String(length=50), nullable=False),
        sa.Column("fieldLabel", sa.String(length=200), nullable=False),
        sa.Column("requiredFlag", sa.Boolean(), nullable=False),
        sa.Column("validationRules", _JSON_OBJECT, nullable=True),
        sa.Column("optionSetID", sa.Uuid(), nullable=True),
        sa.Column("historyTrackedFlag", sa.Boolean(), nullable=False),
        sa.Column("searchableFlag", sa.Boolean(), nullable=False),
        sa.Column("visibilityHints", _JSON_OBJECT, nullable=True),
        sa.Column("userDefinedFlag", sa.Boolean(), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdBy", sa.Uuid(), nullable=True),
        sa.Column("modifiedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modifiedBy", sa.Uuid(), nullable=True),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletedBy", sa.Uuid(), nullable=True),
        sa.Column("rowVersion", sa.Integer(), nullable=False),
        sa.Column("customAttributes", _JSON_OBJECT, nullable=False),
        sa.ForeignKeyConstraint(
            ["optionSetID"],
            ["optionSet.optionSetID"],
            name=op.f("fk_schemaRegistry_optionSetID_optionSet"),
        ),
        sa.PrimaryKeyConstraint("schemaRegistryID", name=op.f("pk_schemaRegistry")),
    )
    with op.batch_alter_table("schemaRegistry", schema=None) as batch_op:
        batch_op.create_index(
            "ix_schemaRegistry_entityType_live",
            ["entityType"],
            unique=False,
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )
        batch_op.create_index(
            batch_op.f("ix_schemaRegistry_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "ix_schemaRegistry_optionSetID_live",
            ["optionSetID"],
            unique=False,
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )
        batch_op.create_index(
            "uq_schemaRegistry_fieldName_live",
            ["fieldName"],
            unique=True,
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )


def downgrade() -> None:
    with op.batch_alter_table("schemaRegistry", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_schemaRegistry_fieldName_live",
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )
        batch_op.drop_index(
            "ix_schemaRegistry_optionSetID_live",
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )
        batch_op.drop_index(batch_op.f("ix_schemaRegistry_modifiedAt"))
        batch_op.drop_index(
            "ix_schemaRegistry_entityType_live",
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )

    op.drop_table("schemaRegistry")
    with op.batch_alter_table("optionValue", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_optionValue_set_name_live",
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )
        batch_op.drop_index(batch_op.f("ix_optionValue_modifiedAt"))

    op.drop_table("optionValue")
    with op.batch_alter_table("workprocessRegistration", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_workprocessRegistration_name_live",
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )
        batch_op.drop_index(batch_op.f("ix_workprocessRegistration_modifiedAt"))

    op.drop_table("workprocessRegistration")
    with op.batch_alter_table("userPreference", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_userPreference_user_key_live",
            sqlite_where=sa.text('"deletedAt" IS NULL AND "userID" IS NOT NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL AND "userID" IS NOT NULL'),
        )
        batch_op.drop_index(
            "uq_userPreference_orgDefault_key_live",
            sqlite_where=sa.text('"deletedAt" IS NULL AND "userID" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL AND "userID" IS NULL'),
        )
        batch_op.drop_index(batch_op.f("ix_userPreference_modifiedAt"))

    op.drop_table("userPreference")
    with op.batch_alter_table("postalCode", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_postalCode_country_value_live",
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )
        batch_op.drop_index(batch_op.f("ix_postalCode_modifiedAt"))

    op.drop_table("postalCode")
    with op.batch_alter_table("optionSet", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_optionSet_optionSetName_live",
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )
        batch_op.drop_index(batch_op.f("ix_optionSet_modifiedAt"))

    op.drop_table("optionSet")
    with op.batch_alter_table("fieldChange", schema=None) as batch_op:
        batch_op.drop_index(
            "ix_fieldChange_record_live",
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )
        batch_op.drop_index(batch_op.f("ix_fieldChange_modifiedAt"))

    op.drop_table("fieldChange")
    with op.batch_alter_table("duplicateOverride", schema=None) as batch_op:
        batch_op.drop_index(
            "ix_duplicateOverride_record_live",
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )
        batch_op.drop_index(batch_op.f("ix_duplicateOverride_modifiedAt"))

    op.drop_table("duplicateOverride")
    with op.batch_alter_table("changeFeedEntry", schema=None) as batch_op:
        batch_op.drop_index(
            "ix_changeFeedEntry_watermark_live",
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )
        batch_op.drop_index(batch_op.f("ix_changeFeedEntry_modifiedAt"))

    op.drop_table("changeFeedEntry")
    with op.batch_alter_table("backgroundJob", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_backgroundJob_modifiedAt"))
        batch_op.drop_index(
            "ix_backgroundJob_lease_live",
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )
        batch_op.drop_index(
            "ix_backgroundJob_expiry",
            sqlite_where=sa.text('"jobExpiresAt" IS NOT NULL'),
            postgresql_where=sa.text('"jobExpiresAt" IS NOT NULL'),
        )
        batch_op.drop_index(
            "ix_backgroundJob_claim_live",
            sqlite_where=sa.text('"deletedAt" IS NULL'),
            postgresql_where=sa.text('"deletedAt" IS NULL'),
        )

    op.drop_table("backgroundJob")
