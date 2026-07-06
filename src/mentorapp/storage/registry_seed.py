"""Source-controlled seeding of built-in schema-registry rows (DB-S6/REQ-050).

The data-model standard requires built-in fields' registry rows to be seeded
from source-controlled definitions in the same change-set that adds the
column, with startup failing on drift (REQ-050, REQ-051). Here the mapped
column IS the source-controlled definition: per-field registry metadata
(label, type, flags, default, help text, option set) is declared at the column site via
``mapped_column(..., info={"registry": {...}})``, so a column and its registry
row can never land in different change-sets. ``seed_built_in_registry``
reconciles the live registry against those definitions at schema-creation/
migration time; ``run_schema_drift_startup_check`` then verifies the result
before the app serves traffic.

Only ``BaseEntity`` subclasses are seeded: platform tables (the registry
itself, jobs, the feed) have no registry rows and no read views, per the
read-surface standard. Built-in choice fields (DB-S7/REQ-055) declare their
option set by name (``optionSet``) with optional seed values
(``optionValues``); the seed creates missing sets/values but never updates or
retires existing ones — relabeling and retirement are admin data operations,
not schema.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Uuid,
    inspect,
    select,
)
from sqlalchemy.orm import Mapper, Session

from mentorapp.observability import get_logger
from mentorapp.storage.base import utcnow
from mentorapp.storage.entity import Base, BaseEntity
from mentorapp.storage.models import OptionSet, OptionValue, SchemaRegistry
from mentorapp.storage.readsurface import STRUCTURAL_COLUMN_NAMES

log = get_logger(__name__)

_INFO_KEY: Final = "registry"
_ALLOWED_INFO_KEYS: Final[frozenset[str]] = frozenset(
    {
        "fieldType",
        "fieldLabel",
        "requiredFlag",
        "defaultValue",
        "helpText",
        "historyTrackedFlag",
        "searchableFlag",
        "optionSet",
        "optionValues",
        "visibilityHints",
    }
)


class RegistrySeedError(RuntimeError):
    """Raised when the source-controlled definitions cannot seed a valid registry."""


@dataclass(frozen=True)
class BuiltInField:
    """One built-in field's registry definition, derived from its mapped column."""

    entity_type: str
    field_name: str
    field_type: str
    field_label: str
    required_flag: bool
    history_tracked_flag: bool
    searchable_flag: bool
    option_set_name: str | None
    option_values: tuple[tuple[str, str], ...]
    visibility_hints: dict[str, Any] | None
    default_value: Any | None = None
    help_text: str | None = None
    # DB-R2b's sanctioned duplicate: this column is a foreign key carrying the
    # identical name as the primary key it references (e.g. sessionLog's
    # crmEngagementRefID). The one case where a fieldName may appear on more
    # than one entity — same name, same meaning.
    r2b_reappearance: bool = False


@dataclass(frozen=True)
class RegistrySeedResult:
    """What one reconciliation run changed, by registry ``fieldName``."""

    inserted: tuple[str, ...]
    updated: tuple[str, ...]
    retired: tuple[str, ...]


def _r2b_reappearance(column: Column[Any]) -> bool:
    # entity_ref derives the FK's name from the referenced key, so name
    # equality is exactly the DB-R2b shape — not a heuristic.
    return any(fk.column.name == column.name for fk in column.foreign_keys)


def _derived_label(field_name: str) -> str:
    # engagementName -> "Engagement Name", mentorID -> "Mentor ID".
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", field_name)
    return spaced[0].upper() + spaced[1:]


def _derived_field_type(column: Column[Any]) -> str:
    if column.primary_key:
        return "id"
    if column.foreign_keys:
        return "reference"
    match column.type:
        case Uuid():
            return "reference"
        case String():
            return "text"
        case Boolean():
            return "boolean"
        case Integer() | Numeric():
            return "number"
        case DateTime():
            return "timestamp"
        case Date():
            return "date"
        case JSON():
            return "json"
    raise RegistrySeedError(
        f"cannot derive a fieldType for column {column.name!r} "
        f"({column.type!r}); declare one in info={{'registry': {{'fieldType': ...}}}}"
    )


def _derived_default_value(column: Column[Any]) -> Any | None:
    # Only a scalar constant is a form default (REQ-033); callable defaults
    # (uuid7, utcnow, dict) are generation logic the registry cannot represent.
    if column.default is not None and column.default.is_scalar:
        return column.default.arg
    return None


def built_in_field_from_column(entity_type: str, column: Column[Any]) -> BuiltInField:
    """Derive one column's registry definition, honoring its ``info['registry']``.

    Raises :class:`RegistrySeedError` on unknown info keys (typo protection —
    a silently ignored flag would drift the registry) and on ``optionValues``
    declared without an ``optionSet``.
    """
    info: dict[str, Any] = dict(column.info.get(_INFO_KEY) or {})
    unknown = sorted(set(info) - _ALLOWED_INFO_KEYS)
    if unknown:
        raise RegistrySeedError(
            f"unknown registry info keys on column {column.name!r}: {unknown}"
        )
    option_set_name: str | None = info.get("optionSet")
    option_values = tuple(
        (str(name), str(label)) for name, label in info.get("optionValues", ())
    )
    if option_values and option_set_name is None:
        raise RegistrySeedError(
            f"column {column.name!r} declares optionValues without an optionSet"
        )
    if "fieldType" in info:
        field_type = str(info["fieldType"])
    elif option_set_name is not None:
        field_type = "choice"
    else:
        field_type = _derived_field_type(column)
    # Required means "the API demands a value": a nullable column, a defaulted
    # column, and the server-assigned key are all satisfiable without input.
    derived_required = not column.nullable and column.default is None and not column.primary_key
    return BuiltInField(
        entity_type=entity_type,
        field_name=column.name,
        field_type=field_type,
        field_label=str(info.get("fieldLabel", _derived_label(column.name))),
        required_flag=bool(info.get("requiredFlag", derived_required)),
        history_tracked_flag=bool(info.get("historyTrackedFlag", False)),
        searchable_flag=bool(info.get("searchableFlag", False)),
        option_set_name=option_set_name,
        option_values=option_values,
        visibility_hints=info.get("visibilityHints"),
        # An explicit declaration wins even when it is None — "no default" is
        # a deliberate override of the column's own default.
        default_value=(
            info["defaultValue"] if "defaultValue" in info else _derived_default_value(column)
        ),
        help_text=str(info["helpText"]) if "helpText" in info else None,
        r2b_reappearance=_r2b_reappearance(column),
    )


def _entity_mappers(entities: Sequence[type[BaseEntity]] | None) -> list[Mapper[Any]]:
    if entities is not None:
        mappers = [inspect(cls) for cls in entities]
    else:
        mappers = [m for m in Base.registry.mappers if issubclass(m.class_, BaseEntity)]
    return sorted(mappers, key=lambda m: m.local_table.name)


def built_in_fields(
    entities: Sequence[type[BaseEntity]] | None = None,
) -> list[BuiltInField]:
    """Every built-in field definition, derived from the mapped entity classes.

    ``entities`` narrows the sweep (a migration seeds the entity it adds);
    None derives from every :class:`BaseEntity` subclass. Structural system
    columns are exempt from registration (DB-R2 exemption), exactly as the
    drift check skips them.
    """
    fields: list[BuiltInField] = []
    for mapper in _entity_mappers(entities):
        table = mapper.local_table
        fields.extend(
            built_in_field_from_column(str(table.name), column)
            for column in table.columns
            if column.name not in STRUCTURAL_COLUMN_NAMES
        )
    return fields


def _ensure_option_set(session: Session, spec: BuiltInField) -> uuid.UUID:
    option_set = session.scalars(
        select(OptionSet).where(
            OptionSet.option_set_name == spec.option_set_name,
            OptionSet.deleted_at.is_(None),
        )
    ).one_or_none()
    if option_set is None:
        option_set = OptionSet(option_set_name=spec.option_set_name)
        session.add(option_set)
        session.flush()
    existing = set(
        session.scalars(
            select(OptionValue.option_value_name).where(
                OptionValue.option_set_id == option_set.option_set_id,
                OptionValue.deleted_at.is_(None),
            )
        )
    )
    # Insert-if-missing only: an admin's relabel or retirement of a live value
    # must survive reseeding (DB-S7 — option lists are data, not schema).
    for sort_order, (name, label) in enumerate(spec.option_values):
        if name not in existing:
            session.add(
                OptionValue(
                    option_set_id=option_set.option_set_id,
                    option_value_name=name,
                    option_value_label=label,
                    option_value_sort_order=sort_order,
                )
            )
    return option_set.option_set_id


def _apply(row: SchemaRegistry, spec: BuiltInField, option_set_id: uuid.UUID | None) -> bool:
    wanted: dict[str, Any] = {
        "entity_type": spec.entity_type,
        "field_type": spec.field_type,
        "field_label": spec.field_label,
        "required_flag": spec.required_flag,
        "default_value": spec.default_value,
        "help_text": spec.help_text,
        "history_tracked_flag": spec.history_tracked_flag,
        "searchable_flag": spec.searchable_flag,
        "option_set_id": option_set_id,
        "visibility_hints": spec.visibility_hints,
    }
    changed = False
    for attr, value in wanted.items():
        if getattr(row, attr) != value:
            setattr(row, attr, value)
            changed = True
    return changed


def seed_built_in_registry(
    session: Session, entities: Sequence[type[BaseEntity]] | None = None
) -> RegistrySeedResult:
    """Reconcile live built-in registry rows against the source-controlled definitions.

    Inserts missing rows, updates changed metadata in place, and soft-retires
    built-in rows whose column no longer exists on a seeded entity. Rows are
    never physically deleted, user-defined rows are never touched, and rows
    for entity types outside this sweep are left for the drift check to judge.
    Raises :class:`RegistrySeedError` when definitions collide with each other
    or with a live user-defined field name (DB-R2 uniqueness) — that conflict
    needs a human, not a silent overwrite. Flushes but does not commit; the
    caller owns the migration transaction.
    """
    fields = built_in_fields(entities)
    names = [spec.field_name for spec in fields]
    # DB-R2: one name, one meaning, system-wide. The single sanctioned
    # duplicate is DB-R2b's key re-appearance — an FK carrying the identical
    # name as the PK it references; anything else colliding is a defect.
    non_r2b = [spec.field_name for spec in fields if not spec.r2b_reappearance]
    duplicates = sorted({name for name in non_r2b if non_r2b.count(name) > 1})
    if duplicates:
        raise RegistrySeedError(f"built-in field names collide across entities: {duplicates}")

    live_rows = session.scalars(
        select(SchemaRegistry).where(SchemaRegistry.deleted_at.is_(None))
    ).all()
    built_in_rows = {
        (row.entity_type, row.field_name): row for row in live_rows if not row.user_defined_flag
    }
    user_defined_names = {row.field_name for row in live_rows if row.user_defined_flag}
    collisions = sorted(set(names) & user_defined_names)
    if collisions:
        raise RegistrySeedError(
            f"built-in fields collide with live user-defined fields: {collisions}"
        )
    # Cross-entity collisions with live rows outside this sweep: allowed only
    # for the R2b shape (the spec is a key re-appearance, or the foreign rows
    # are reference re-appearances of the spec's own key).
    for spec in fields:
        if spec.r2b_reappearance:
            continue
        foreign = [
            row
            for (entity_type, name), row in built_in_rows.items()
            if name == spec.field_name and entity_type != spec.entity_type
        ]
        if any(row.field_type != "reference" for row in foreign):
            raise RegistrySeedError(
                f"built-in field {spec.field_name!r} collides with a live "
                f"registry row on another entity"
            )

    inserted: list[str] = []
    updated: list[str] = []
    for spec in fields:
        option_set_id = (
            _ensure_option_set(session, spec) if spec.option_set_name is not None else None
        )
        row = built_in_rows.get((spec.entity_type, spec.field_name))
        if row is None:
            session.add(
                SchemaRegistry(
                    entity_type=spec.entity_type,
                    field_name=spec.field_name,
                    field_type=spec.field_type,
                    field_label=spec.field_label,
                    required_flag=spec.required_flag,
                    default_value=spec.default_value,
                    help_text=spec.help_text,
                    history_tracked_flag=spec.history_tracked_flag,
                    searchable_flag=spec.searchable_flag,
                    option_set_id=option_set_id,
                    visibility_hints=spec.visibility_hints,
                )
            )
            inserted.append(spec.field_name)
        elif _apply(row, spec, option_set_id):
            updated.append(spec.field_name)

    retired: list[str] = []
    wanted = {(spec.entity_type, spec.field_name) for spec in fields}
    seeded_entity_types = {spec.entity_type for spec in fields}
    for (entity_type, name), row in built_in_rows.items():
        if entity_type in seeded_entity_types and (entity_type, name) not in wanted:
            # StructuralColumnsMixin has no soft_delete helper; stamp directly.
            row.deleted_at = utcnow()
            retired.append(name)

    session.flush()
    result = RegistrySeedResult(
        inserted=tuple(sorted(inserted)),
        updated=tuple(sorted(updated)),
        retired=tuple(sorted(retired)),
    )
    log.info(
        "built-in registry seeded",
        extra={
            "context": {
                "entityTypes": sorted(seeded_entity_types),
                "inserted": list(result.inserted),
                "updated": list(result.updated),
                "retired": list(result.retired),
            }
        },
    )
    return result
