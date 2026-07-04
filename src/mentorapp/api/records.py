"""Shared record primitives: registry lookup, field-to-column maps, serialization.

The read half of custom-attribute handling (REQ-049, DB-R3) lives here:
:func:`serialize_record` merges registered custom attributes from the
``customAttributes`` bag into the served record, flat — the UI never cares
whether a field is built-in or user-defined. System-wide field-name
uniqueness (DB-R2) is what makes the flat merge collision-free.

Field names are the wire vocabulary everywhere (camelCase database column
names per DB-R2); the maps here translate them to ORM attributes once, so
the list and write engines never hand-maintain a name table.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy import inspect, select
from sqlalchemy.orm import InstrumentedAttribute, Session, selectinload

from mentorapp.storage import OptionSet, SchemaRegistry

# The DB-R2 exemption set: identical on every table, API-maintained, never
# writable through the field-level contract (rowVersion travels beside the
# payload, not inside it).
STRUCTURAL_FIELDS: Final[frozenset[str]] = frozenset(
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


def registry_for(session: Session, entity_type: str) -> dict[str, SchemaRegistry]:
    """All live registry rows for one entity, keyed by ``fieldName`` (DB-S6).

    Option sets are eagerly loaded because choice validation and duplicate
    detection both read them; one query pattern serves every caller.
    """
    rows = session.scalars(
        select(SchemaRegistry)
        .where(
            SchemaRegistry.entity_type == entity_type,
            SchemaRegistry.deleted_at.is_(None),
        )
        .options(selectinload(SchemaRegistry.option_set).selectinload(OptionSet.option_values))
    ).all()
    return {row.field_name: row for row in rows}


def columns_by_field_name(entity_cls: type[Any]) -> dict[str, InstrumentedAttribute[Any]]:
    """Map wire field names (database column names) to queryable ORM attributes."""
    mapper = inspect(entity_cls)
    return {prop.columns[0].name: getattr(entity_cls, prop.key) for prop in mapper.column_attrs}


def attribute_keys_by_field_name(entity_cls: type[Any]) -> dict[str, str]:
    """Map wire field names to the Python attribute names used by the ORM."""
    mapper = inspect(entity_cls)
    return {prop.columns[0].name: prop.key for prop in mapper.column_attrs}


def primary_key_field(entity_cls: type[Any]) -> tuple[str, InstrumentedAttribute[uuid.UUID]]:
    """The entity-named primary key as ``(fieldName, ORM attribute)`` (DB-R2b).

    Every entity has exactly one UUIDv7 key (DB-R1), so the single-column
    assumption is a standard, not a guess.
    """
    mapper = inspect(entity_cls)
    pk_column = mapper.primary_key[0]
    prop = mapper.get_property_by_column(pk_column)
    return pk_column.name, getattr(entity_cls, prop.key)


def record_id_of(record: Any) -> uuid.UUID:
    """The record's primary-key value, via the entity's key metadata."""
    _, pk_attr = primary_key_field(type(record))
    return getattr(record, pk_attr.key)


def serialize_record(record: Any) -> dict[str, Any]:
    """One served record: built-in columns by wire name + custom attributes, flat.

    The raw ``customAttributes`` bag is not served — its registered members are
    promoted to top-level fields (DB-R3), exactly as the generated read views
    promote them (DB-S9). ``rowVersion`` rides along so any read can lead to an
    edit (DB-S4). Values stay native (UUID/datetime); the envelope layer's
    ``jsonable_encoder`` owns wire formatting.
    """
    mapper = inspect(type(record))
    payload: dict[str, Any] = {
        prop.columns[0].name: getattr(record, prop.key)
        for prop in mapper.column_attrs
        if prop.columns[0].name != "customAttributes"
    }
    payload.update(getattr(record, "custom_attributes", None) or {})
    return payload
