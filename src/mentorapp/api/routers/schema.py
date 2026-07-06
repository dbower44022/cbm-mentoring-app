"""``GET /schema/{entity}`` — the one metadata endpoint over the schema registry.

Serves every registry row for one entity, built-in and user-defined alike
(REQ-050, DB-S6). This single contract drives server-driven UI rendering,
API validation, duplicate-detection configuration, history flags, export
columns, and admin-SQL view columns — the anti-enum-drift design.

Choice fields carry their option set inline: the dropdown and the validator
read the same rows (DB-S7), so a second option-list contract cannot drift.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from mentorapp.api.deps import get_session
from mentorapp.api.envelope import Envelope, ok
from mentorapp.api.errors import RecordNotFoundError
from mentorapp.storage import OptionSet, SchemaRegistry

router = APIRouter()


def _option_set_payload(option_set: OptionSet) -> dict[str, Any]:
    # Retired values (activeFlag off) are still served: hidden from new entry by
    # the client, but historical records storing their optionValueID must render.
    # Soft-deleted rows are not served anywhere (DB-S3).
    values = [
        {
            "optionValueID": str(value.option_value_id),
            "optionValueName": value.option_value_name,
            "optionValueLabel": value.option_value_label,
            "optionValueSortOrder": value.option_value_sort_order,
            "activeFlag": value.active_flag,
        }
        for value in option_set.option_values
        if value.deleted_at is None
    ]
    return {
        "optionSetID": str(option_set.option_set_id),
        "optionSetName": option_set.option_set_name,
        "optionValues": values,
    }


def _field_payload(row: SchemaRegistry) -> dict[str, Any]:
    return {
        "fieldName": row.field_name,
        "fieldType": row.field_type,
        "fieldLabel": row.field_label,
        "requiredFlag": row.required_flag,
        "validationRules": row.validation_rules,
        # The create form's prefill source (REQ-037): a default is a field
        # setting, never a per-form constant.
        "defaultValue": row.default_value,
        "historyTrackedFlag": row.history_tracked_flag,
        "searchableFlag": row.searchable_flag,
        "visibilityHints": row.visibility_hints,
        "userDefinedFlag": row.user_defined_flag,
        "optionSet": _option_set_payload(row.option_set) if row.option_set else None,
    }


@router.get("/schema/{entity_type}")
def get_entity_schema(
    entity_type: str, session: Annotated[Session, Depends(get_session)]
) -> Envelope:
    """Describe every live field of one entity from the schema registry.

    Returns ``data.entityType`` plus ``data.fields`` (ordered by ``fieldName``
    for a deterministic contract; display order is the client's job via
    ``visibilityHints``). 404 when the entity has no live registry rows —
    an entity with zero fields cannot exist, so no-rows means unknown entity.
    """
    rows = session.scalars(
        select(SchemaRegistry)
        .where(
            SchemaRegistry.entity_type == entity_type,
            SchemaRegistry.deleted_at.is_(None),
        )
        .order_by(SchemaRegistry.field_name)
        .options(selectinload(SchemaRegistry.option_set).selectinload(OptionSet.option_values))
    ).all()
    if not rows:
        raise RecordNotFoundError("entity schema", entity_type)
    fields = [_field_payload(row) for row in rows]
    return ok(
        data={"entityType": entity_type, "fields": fields},
        meta={"fieldCount": len(fields)},
    )
