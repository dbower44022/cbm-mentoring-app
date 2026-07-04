"""Storage layer — base-entity model, key policy, registries, jobs, and change feed.

Implements the ENG-004 data-model standard: ``entity`` carries the declarative
base, structural system columns, and key-naming policy (WTK-125); ``models``
carries the schema registry, option sets, background jobs, and change feed
(WTK-126/WTK-127). One declarative ``Base`` spans them all.
"""

from mentorapp.storage.adminsql import (
    ADMIN_SQL_ROLE,
    ADMIN_SQL_STATEMENT_TIMEOUT_MS,
    CURRENT_USER_PARAM,
    AdminSqlError,
    AdminSqlSource,
    admin_sql_role_ddl,
    execute_admin_sql,
    validate_admin_sql,
)
from mentorapp.storage.base import StructuralColumnsMixin, utcnow
from mentorapp.storage.entity import (
    Base,
    BaseEntity,
    entity_key,
    entity_ref,
    live_index,
    live_unique,
)
from mentorapp.storage.ids import uuid7, uuid7_created_at
from mentorapp.storage.models import (
    CHANGE_KINDS,
    JOB_STATUSES,
    SELECTION_CONTRACTS,
    BackgroundJob,
    ChangeFeedEntry,
    DuplicateOverride,
    FieldChange,
    OptionSet,
    OptionValue,
    PostalCode,
    SchemaRegistry,
    UserPreference,
    WorkprocessRegistration,
)
from mentorapp.storage.readsurface import (
    STRUCTURAL_COLUMN_NAMES,
    DriftFinding,
    SchemaDriftError,
    generate_read_view_sql,
    partial_index_rule_violations,
    read_view_name,
    regenerate_read_views,
    run_schema_drift_startup_check,
    schema_drift_findings,
)

__all__ = [
    "ADMIN_SQL_ROLE",
    "ADMIN_SQL_STATEMENT_TIMEOUT_MS",
    "CHANGE_KINDS",
    "CURRENT_USER_PARAM",
    "JOB_STATUSES",
    "SELECTION_CONTRACTS",
    "STRUCTURAL_COLUMN_NAMES",
    "AdminSqlError",
    "AdminSqlSource",
    "BackgroundJob",
    "Base",
    "BaseEntity",
    "ChangeFeedEntry",
    "DriftFinding",
    "DuplicateOverride",
    "FieldChange",
    "OptionSet",
    "OptionValue",
    "PostalCode",
    "SchemaDriftError",
    "SchemaRegistry",
    "StructuralColumnsMixin",
    "UserPreference",
    "WorkprocessRegistration",
    "admin_sql_role_ddl",
    "entity_key",
    "entity_ref",
    "execute_admin_sql",
    "generate_read_view_sql",
    "live_index",
    "live_unique",
    "partial_index_rule_violations",
    "read_view_name",
    "regenerate_read_views",
    "run_schema_drift_startup_check",
    "schema_drift_findings",
    "utcnow",
    "uuid7",
    "uuid7_created_at",
    "validate_admin_sql",
]
