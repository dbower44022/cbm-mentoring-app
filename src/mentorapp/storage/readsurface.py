"""The generated read surface: per-entity views, drift check, partial-index rule.

Implements the read-surface standard (DB-S9/REQ-056) and the storage-wide
enforcement hooks for soft delete (DB-S3/REQ-052) and the schema registry
(DB-S6/REQ-050):

- ``generate_read_view_sql``/``regenerate_read_views`` — for every entity
  named in the schema registry, a generated view (``vw<Entity>``) with base
  columns, option-value labels joined in, registered custom attributes
  promoted from JSONB to named columns, and soft-deleted rows excluded.
  Views are the official read surface: app reads and admin-authored SQL
  target views, never base tables. Regeneration is part of the
  custom-attribute lifecycle — call it after any registry mutation.
- ``run_schema_drift_startup_check`` — built-in registry rows are seeded by
  the same migration that adds the column; startup fails on drift between
  the registry and the actual database schema.
- ``partial_index_rule_violations`` — the mechanical form of REQ-052's
  "all indexes and unique constraints are partial over live rows"; the test
  suite asserts zero violations over the full metadata, so a non-partial
  index cannot land unnoticed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from sqlalchemy import MetaData, UniqueConstraint, inspect, select, text
from sqlalchemy.orm import Session

from mentorapp.observability import get_logger
from mentorapp.storage.entity import Base
from mentorapp.storage.models import SchemaRegistry

log = get_logger(__name__)

# The eight structural system columns (DB-R2 exemption): identical on every
# table, never registry rows, so the drift check skips them.
STRUCTURAL_COLUMN_NAMES: Final[frozenset[str]] = frozenset(
    {
        "createdAt",
        "createdBy",
        "modifiedAt",
        "modifiedBy",
        "deletedAt",
        "deletedBy",
        "rowVersion",
        "customAttributes",
    }
)

# modifiedAt indexes are deliberately NOT partial: the change feed must surface
# soft deletes and restores (DB-S10 overrides the DB-S3 partial rule here).
_PARTIAL_RULE_EXEMPT_COLUMNS: Final[frozenset[str]] = frozenset({"modifiedAt"})

# Registry names reach generated SQL as identifiers; anything else is rejected
# before it can become an injection vector (admins create registry rows).
_SAFE_SQL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")


class SchemaDriftError(RuntimeError):
    """Raised at startup when the schema registry and the database disagree."""

    def __init__(self, findings: list[DriftFinding]) -> None:
        self.findings = findings
        detail = "; ".join(
            f"{f.entity_type}.{f.field_name or '-'}: {f.problem}" for f in findings
        )
        super().__init__(f"schema registry drift: {detail}")


@dataclass(frozen=True)
class DriftFinding:
    """One registry-vs-schema disagreement found by the startup check."""

    entity_type: str
    field_name: str | None
    problem: str  # missingTable | missingColumn | unregisteredColumn


def read_view_name(entity_type: str) -> str:
    """The generated view's name for one entity — e.g. ``vwMentor`` (DB-S9)."""
    return f"vw{entity_type}"


def _checked_name(name: str, what: str) -> str:
    if not _SAFE_SQL_NAME.match(name):
        raise ValueError(f"unsafe {what} for generated SQL: {name!r}")
    return name


def _custom_attribute_expr(field_name: str, *, dialect_name: str) -> str:
    """The JSONB member as a scalar expression, per dialect."""
    if dialect_name == "postgresql":
        return f"t.\"customAttributes\" ->> '{field_name}'"
    return f"json_extract(t.\"customAttributes\", '$.{field_name}')"


def _option_id_expr(row: SchemaRegistry, *, dialect_name: str) -> str:
    """The stored optionValueID for a choice field, join-comparable per dialect.

    Built-in choice fields store the ID in a real Uuid column. User-defined
    ones store it in JSON as the canonical dashed string; Postgres casts it
    back to uuid, SQLite compares against the generic Uuid type's dashless
    CHAR(32) storage.
    """
    if not row.user_defined_flag:
        return f't."{row.field_name}"'
    json_expr = _custom_attribute_expr(row.field_name, dialect_name=dialect_name)
    if dialect_name == "postgresql":
        return f"({json_expr})::uuid"
    return f"replace({json_expr}, '-', '')"


def generate_read_view_sql(
    entity_type: str, registry_rows: list[SchemaRegistry], *, dialect_name: str
) -> str:
    """The ``CREATE VIEW`` statement for one entity's generated read view.

    Base columns come first, then promoted custom attributes (named by their
    registry ``fieldName`` — unique system-wide, so the view reads like the
    business domain), then one ``<fieldName>Label`` per choice field. The
    ``deletedAt IS NULL`` filter is baked in: view readers cannot see
    soft-deleted rows (REQ-052's central read rule for this surface).
    """
    table = Base.metadata.tables.get(_checked_name(entity_type, "entity type"))
    if table is None:
        raise SchemaDriftError([DriftFinding(entity_type, None, "missingTable")])

    select_parts = [f't."{col.name}"' for col in table.columns]
    joins: list[str] = []
    # Deterministic order keeps regenerated view DDL diffable run to run.
    for row in sorted(registry_rows, key=lambda r: r.field_name):
        field = _checked_name(row.field_name, "field name")
        if row.user_defined_flag:
            expr = _custom_attribute_expr(field, dialect_name=dialect_name)
            select_parts.append(f'{expr} AS "{field}"')
        if row.option_set_id is not None:
            alias = f"opt_{field}"
            id_expr = _option_id_expr(row, dialect_name=dialect_name)
            joins.append(
                f'LEFT JOIN "optionValue" AS "{alias}"\n'
                f'    ON "{alias}"."optionValueID" = {id_expr}'
                f' AND "{alias}"."deletedAt" IS NULL'
            )
            select_parts.append(f'"{alias}"."optionValueLabel" AS "{field}Label"')

    lines = [
        f'CREATE VIEW "{read_view_name(entity_type)}" AS',
        "SELECT " + ",\n       ".join(select_parts),
        f'FROM "{table.name}" AS t',
        *joins,
        'WHERE t."deletedAt" IS NULL',
    ]
    return "\n".join(lines)


def regenerate_read_views(session: Session) -> list[str]:
    """Drop and recreate every generated read view; returns the view names.

    Called at startup (after the drift check) and after any schema-registry
    mutation — adding a custom attribute regenerates its entity's view
    automatically, per the custom-attribute lifecycle (DB-S9). One view per
    distinct live registry ``entityType``; platform tables have no registry
    rows and therefore no views.
    """
    dialect_name = session.get_bind().dialect.name
    rows = session.scalars(
        select(SchemaRegistry).where(SchemaRegistry.deleted_at.is_(None))
    ).all()
    by_entity: dict[str, list[SchemaRegistry]] = {}
    for row in rows:
        by_entity.setdefault(row.entity_type, []).append(row)

    view_names: list[str] = []
    for entity_type in sorted(by_entity):
        view_name = read_view_name(_checked_name(entity_type, "entity type"))
        ddl = generate_read_view_sql(
            entity_type, by_entity[entity_type], dialect_name=dialect_name
        )
        session.execute(text(f'DROP VIEW IF EXISTS "{view_name}"'))
        session.execute(text(ddl))
        view_names.append(view_name)
    log.info(
        "read views regenerated",
        extra={"context": {"viewCount": len(view_names), "views": view_names}},
    )
    return view_names


def schema_drift_findings(session: Session) -> list[DriftFinding]:
    """Compare live built-in registry rows against the actual database schema.

    Two directions, per entity type named in the registry: every built-in
    (``userDefinedFlag`` false) row must have a matching real column, and
    every non-structural real column must have a live built-in row.
    User-defined rows live in ``customAttributes`` JSONB, so they are outside
    the column comparison by design.
    """
    inspector = inspect(session.get_bind())
    rows = session.scalars(
        select(SchemaRegistry).where(SchemaRegistry.deleted_at.is_(None))
    ).all()
    by_entity: dict[str, list[SchemaRegistry]] = {}
    for row in rows:
        by_entity.setdefault(row.entity_type, []).append(row)

    findings: list[DriftFinding] = []
    for entity_type in sorted(by_entity):
        if not inspector.has_table(entity_type):
            findings.append(DriftFinding(entity_type, None, "missingTable"))
            continue
        actual = {col["name"] for col in inspector.get_columns(entity_type)}
        built_in = {
            row.field_name for row in by_entity[entity_type] if not row.user_defined_flag
        }
        findings.extend(
            DriftFinding(entity_type, name, "missingColumn")
            for name in sorted(built_in - actual)
        )
        findings.extend(
            DriftFinding(entity_type, name, "unregisteredColumn")
            for name in sorted(actual - built_in - STRUCTURAL_COLUMN_NAMES)
        )
    return findings


def run_schema_drift_startup_check(session: Session) -> None:
    """Fail startup on registry drift (DB-S6/REQ-050); log the all-clear otherwise.

    Deploy wiring calls this once an engine exists, before serving traffic and
    before ``regenerate_read_views`` — a drifted registry must never generate
    a wrong view.
    """
    findings = schema_drift_findings(session)
    if findings:
        log.error(
            "schema registry drift detected — refusing to start",
            extra={
                "context": {
                    "findings": [
                        {
                            "entityType": f.entity_type,
                            "fieldName": f.field_name,
                            "problem": f.problem,
                        }
                        for f in findings
                    ]
                }
            },
        )
        raise SchemaDriftError(findings)
    log.info("schema registry drift check passed")


def partial_index_rule_violations(metadata: MetaData | None = None) -> list[str]:
    """Indexes/constraints on soft-delete tables that break the partial rule.

    REQ-052: every index and unique constraint is partial over live rows. A
    violation is a ``UniqueConstraint`` (never partial — use ``live_unique``)
    or an index with no partial predicate at all, unless its columns are all
    exempt (``modifiedAt``, the change-feed override). An index carrying any
    explicit predicate made a deliberate scan-shape choice (e.g. the job
    retention scan) and passes. Tables without ``deletedAt`` are out of scope.
    """
    checked = metadata if metadata is not None else Base.metadata
    violations: list[str] = []
    for table in checked.tables.values():
        if "deletedAt" not in table.columns:
            continue
        violations.extend(
            f"{table.name}.{constraint.name}: UniqueConstraint cannot be partial"
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        )
        for index in table.indexes:
            has_predicate = bool(
                index.dialect_options["postgresql"]["where"] is not None
                and index.dialect_options["sqlite"]["where"] is not None
            )
            if has_predicate:
                continue
            column_names = {col.name for col in index.columns}
            if column_names and column_names <= _PARTIAL_RULE_EXEMPT_COLUMNS:
                continue
            violations.append(f"{table.name}.{index.name}: no live-row predicate")
    return sorted(violations)
