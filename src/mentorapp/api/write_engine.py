"""The shared write engine (REQ-053/054/059, DB-S4/S5/S12): every write speaks this.

The write contract, built once:

- :func:`create_record` — POST is the only whole-record write. Registry
  validation reports ALL field failures in one round trip; duplicate
  detection runs server-side against the entity's registry-declared match
  rules and rejects with the candidate records; an explicit override creates
  the record AND records the override in the same transaction.
- :func:`partial_update` — PATCH is the primary write verb: only changed
  fields travel, plus ``rowVersion``. A stale version raises with the
  current record for the 409 body (DB-S4); unchanged values never dirty the
  row, write history, or bump the version.
- Both paths stamp the audit columns, write ``fieldChange`` rows for
  history-tracked fields, and append the change-feed entry in the same
  transaction as the record write (DB-S5, DB-S10) — a committed write is
  never missing its history or its feed event.

Custom attributes are validated against the schema registry exactly like
built-in fields (REQ-049) and merged key-level into the ``customAttributes``
bag; concurrency for the bag is record-level via ``rowVersion`` (DB-S4).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Protocol

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from mentorapp.api.envelope import ApiError, field_error
from mentorapp.api.errors import (
    ApiValidationError,
    DuplicateCandidatesError,
    RecordNotFoundError,
    StaleRowVersionError,
)
from mentorapp.api.records import (
    STRUCTURAL_FIELDS,
    attribute_keys_by_field_name,
    columns_by_field_name,
    record_id_of,
    registry_for,
    serialize_record,
)

# The ONE definition of duplicate-match equality (DB-S13) lives with the shared
# normalization services (WTK-132) so background jobs and this engine can never
# disagree; re-exported here because it is part of this engine's contract.
from mentorapp.automation.normalization import normalize_for_match
from mentorapp.storage import (
    ChangeFeedEntry,
    DuplicateOverride,
    FieldChange,
    SchemaRegistry,
    utcnow,
)

CODE_UNKNOWN_FIELD = "unknownField"
CODE_READ_ONLY_FIELD = "readOnlyField"
CODE_REQUIRED_FIELD = "requiredField"
CODE_TYPE_MISMATCH = "typeMismatch"
CODE_UNKNOWN_OPTION = "unknownOption"
CODE_INACTIVE_OPTION = "inactiveOption"

# fieldType vocabulary shared with the schema registry; validated here, never
# a database enum (DB-S7). Text-like types differ only in normalization.
_TEXT_TYPES = frozenset({"text", "email", "phone"})


class OptionValueRule(Protocol):
    """The slice of an option value :func:`validate_value` reads (DB-S7)."""

    @property
    def option_value_id(self) -> object: ...
    @property
    def active_flag(self) -> bool: ...
    @property
    def deleted_at(self) -> object | None: ...


class OptionSetRule(Protocol):
    """The slice of an option set :func:`validate_value` reads."""

    @property
    def option_values(self) -> Sequence[OptionValueRule]: ...


class FieldRule(Protocol):
    """The field settings :func:`validate_value` reads — satisfied by the
    ``SchemaRegistry`` row here AND by ``form_validation.FieldSettings`` built
    from the ``GET /schema/{entity}`` payload, so form-side and API-side
    validation cannot diverge (REQ-033)."""

    @property
    def field_name(self) -> str: ...
    @property
    def field_type(self) -> str: ...
    @property
    def required_flag(self) -> bool: ...
    @property
    def option_set(self) -> OptionSetRule | None: ...


def validate_value(row: FieldRule, value: Any) -> ApiError | None:
    """Validate ONE value against ONE field's settings — the single definition.

    Returns the structured :class:`ApiError` (``requiredField``,
    ``typeMismatch``, ``unknownOption``, ``inactiveOption``) or ``None`` when
    the value is acceptable. ``None`` values fail only on required fields;
    choice values must be a live, active ``optionValueID`` from the field's
    option set. Every validation path — create, PATCH, and the form engine's
    on-exit/save-sweep checks — calls this one function (REQ-033).
    """
    if value is None:
        if row.required_flag:
            return field_error(row.field_name, CODE_REQUIRED_FIELD, "This field is required.")
        return None
    field_type = row.field_type
    if field_type in _TEXT_TYPES and not isinstance(value, str):
        return field_error(row.field_name, CODE_TYPE_MISMATCH, "Expected text.")
    is_number = not isinstance(value, bool) and isinstance(value, int | float)
    if field_type == "number" and not is_number:
        return field_error(row.field_name, CODE_TYPE_MISMATCH, "Expected a number.")
    if field_type == "boolean" and not isinstance(value, bool):
        return field_error(row.field_name, CODE_TYPE_MISMATCH, "Expected true or false.")
    if field_type in ("date", "datetime"):
        parser = date.fromisoformat if field_type == "date" else datetime.fromisoformat
        try:
            parser(value)
        except (TypeError, ValueError):
            return field_error(
                row.field_name, CODE_TYPE_MISMATCH, f"Expected an ISO {field_type}."
            )
    if field_type == "choice":
        return _validate_choice(row, value)
    return None


def _validate_choice(row: FieldRule, value: Any) -> ApiError | None:
    # Records store the optionValueID (DB-S7); the validator reads the same
    # option rows the dropdown serves, so drift is impossible by construction.
    live_values = [
        option
        for option in (row.option_set.option_values if row.option_set else [])
        if option.deleted_at is None
    ]
    match = next(
        (option for option in live_values if str(option.option_value_id) == str(value)), None
    )
    if match is None:
        return field_error(row.field_name, CODE_UNKNOWN_OPTION, "Not a value in this list.")
    if not match.active_flag:
        # Historical records may hold retired values; NEW writes may not.
        return field_error(row.field_name, CODE_INACTIVE_OPTION, "This value has been retired.")
    return None


def _validate_changes(
    registry: dict[str, SchemaRegistry], changes: dict[str, Any], *, creating: bool
) -> tuple[dict[str, Any], dict[str, Any], list[ApiError]]:
    """Split a payload into (built-in, custom) buckets, accumulating ALL failures."""
    builtin: dict[str, Any] = {}
    custom: dict[str, Any] = {}
    errors: list[ApiError] = []
    for name, value in changes.items():
        if name in STRUCTURAL_FIELDS:
            # rowVersion travels beside the payload; audit columns are stamped
            # by this engine — a client writing them is a contract violation.
            errors.append(
                field_error(name, CODE_READ_ONLY_FIELD, "System fields cannot be written.")
            )
            continue
        row = registry.get(name)
        if row is None:
            errors.append(field_error(name, CODE_UNKNOWN_FIELD, "No such field."))
            continue
        error = validate_value(row, value)
        if error is not None:
            errors.append(error)
            continue
        (custom if row.user_defined_flag else builtin)[name] = value
    if creating:
        errors.extend(
            field_error(name, CODE_REQUIRED_FIELD, "This field is required.")
            for name, row in registry.items()
            if row.required_flag and changes.get(name) is None
        )
    return builtin, custom, errors


def _match_rules(registry: dict[str, SchemaRegistry]) -> dict[str, list[SchemaRegistry]]:
    # A match rule is declared in the registry as validationRules.duplicateMatchRules
    # on each participating field; fields sharing a rule name form the rule's
    # field set (e.g. byNamePhone on both mentorName and mentorPhone). Entity-level
    # config thus rides the existing per-field registry — no new table (DB-S6).
    rules: dict[str, list[SchemaRegistry]] = {}
    for row in registry.values():
        for rule_name in (row.validation_rules or {}).get("duplicateMatchRules", []):
            rules.setdefault(str(rule_name), []).append(row)
    return rules


def _duplicate_candidates(
    session: Session,
    entity_cls: type[Any],
    registry: dict[str, SchemaRegistry],
    values: dict[str, Any],
) -> tuple[list[Any], list[str]]:
    columns = columns_by_field_name(entity_cls)
    candidates: dict[uuid.UUID, Any] = {}
    matched_rule_names: list[str] = []
    for rule_name, rows in sorted(_match_rules(registry).items()):
        # A rule fires only when the create supplies every one of its fields.
        if any(values.get(row.field_name) is None for row in rows):
            continue
        stmt = select(entity_cls).where(columns["deletedAt"].is_(None))
        for row in rows:
            needle = normalize_for_match(row.field_type, values[row.field_name])
            # Detection runs against indexed normalized shadow columns
            # ("<fieldName>Normalized") when the entity declares one; the
            # lower(trim(col)) fallback computes the same normalization inline
            # for text/email fields. Phone rules REQUIRE the shadow column —
            # digits-only cannot be expressed portably inline, and a silently
            # weaker match is worse than a loud missing column.
            shadow = columns.get(f"{row.field_name}Normalized")
            if shadow is not None:
                stmt = stmt.where(shadow == needle)
            elif row.field_type == "phone":
                raise RuntimeError(
                    f"duplicate rule {rule_name!r} needs a {row.field_name}Normalized column"
                )
            else:
                stmt = stmt.where(func.lower(func.trim(columns[row.field_name])) == needle)
        found_any = False
        for record in session.scalars(stmt):
            candidates[record_id_of(record)] = record
            found_any = True
        if found_any:
            matched_rule_names.append(rule_name)
    return list(candidates.values()), matched_rule_names


def _append_feed_entry(
    session: Session, entity_type: str, record: Any, change_kind: str
) -> None:
    # Same transaction as the record write (DB-S10): the feed can never miss a
    # committed change, and a rolled-back change never surfaces.
    session.add(
        ChangeFeedEntry(
            entity_type=entity_type,
            record_id=record_id_of(record),
            record_row_version=record.row_version,
            change_kind=change_kind,
        )
    )


def create_record(
    session: Session,
    entity_cls: type[Any],
    entity_type: str,
    values: dict[str, Any],
    *,
    acting_user_id: uuid.UUID | None = None,
    override_duplicates: bool = False,
    override_reason: str | None = None,
) -> Any:
    """POST create — validate, detect duplicates, stamp, and feed, in one transaction.

    Inputs are wire field names; ``values`` may mix built-in and custom fields
    freely. Raises :class:`ApiValidationError` (every failure at once),
    :class:`DuplicateCandidatesError` (candidates in the 409 body) unless
    ``override_duplicates`` — an override is itself recorded (REQ-059).
    Returns the flushed record; the caller owns the commit.
    """
    registry = registry_for(session, entity_type)
    builtin, custom, errors = _validate_changes(registry, values, creating=True)
    if errors:
        raise ApiValidationError(errors)

    candidates, matched_rule_names = _duplicate_candidates(
        session, entity_cls, registry, values
    )
    if candidates and not override_duplicates:
        raise DuplicateCandidatesError([serialize_record(record) for record in candidates])

    attribute_keys = attribute_keys_by_field_name(entity_cls)
    record = entity_cls(**{attribute_keys[name]: value for name, value in builtin.items()})
    record.custom_attributes = dict(custom)
    record.created_by = acting_user_id
    record.modified_by = acting_user_id
    session.add(record)
    session.flush()

    if candidates:
        session.add(
            DuplicateOverride(
                entity_type=entity_type,
                record_id=record_id_of(record),
                matched_rule_names=matched_rule_names,
                candidate_record_ids=[str(record_id_of(c)) for c in candidates],
                override_reason=override_reason,
                created_by=acting_user_id,
                modified_by=acting_user_id,
            )
        )
    # Field history starts at the first CHANGE (DB-S5 tracks transitions); the
    # created feed entry + audit columns already record the birth.
    _append_feed_entry(session, entity_type, record, "created")
    session.flush()
    return record


def partial_update(
    session: Session,
    record: Any,
    entity_type: str,
    changes: dict[str, Any],
    *,
    row_version: int,
    acting_user_id: uuid.UUID | None = None,
) -> Any:
    """PATCH — apply only the changed fields, guarded by ``rowVersion`` (DB-S4/S12).

    Raises :class:`StaleRowVersionError` carrying the current record (the 409
    body), :class:`ApiValidationError` with every field failure at once, and
    :class:`RecordNotFoundError` for a soft-deleted target. History rows are
    written for history-tracked fields that actually changed; a no-op payload
    leaves the record untouched (no version bump, no feed entry).
    """
    if record.deleted_at is not None:
        raise RecordNotFoundError(entity_type, str(record_id_of(record)))
    if row_version != record.row_version:
        raise StaleRowVersionError(serialize_record(record))

    registry = registry_for(session, entity_type)
    builtin, custom, errors = _validate_changes(registry, changes, creating=False)
    if errors:
        raise ApiValidationError(errors)

    attribute_keys = attribute_keys_by_field_name(type(record))
    history: list[tuple[str, Any, Any]] = []
    for name, value in builtin.items():
        old_value = getattr(record, attribute_keys[name])
        if old_value == value:
            continue
        setattr(record, attribute_keys[name], value)
        if registry[name].history_tracked_flag:
            history.append((name, old_value, value))
    if custom:
        merged = dict(record.custom_attributes)
        for name, value in custom.items():
            old_value = merged.get(name)
            if old_value == value:
                continue
            merged[name] = value
            if registry[name].history_tracked_flag:
                history.append((name, old_value, value))
        if merged != record.custom_attributes:
            # Reassigned (not mutated) so the ORM sees the change; the bag
            # versions at record level via rowVersion (DB-S4).
            record.custom_attributes = merged

    if not session.is_modified(record):
        return record

    record.modified_by = acting_user_id
    record.modified_at = utcnow()
    try:
        # version_id_col turns this into UPDATE ... WHERE rowVersion = <read
        # version>, closing the race the precheck above cannot see: a write
        # committed by another transaction between our read and this flush.
        session.flush()
    except StaleDataError as exc:
        session.rollback()
        current = session.get(type(record), record_id_of(record))
        if current is None or current.deleted_at is not None:
            raise RecordNotFoundError(entity_type, str(record_id_of(record))) from exc
        raise StaleRowVersionError(serialize_record(current)) from exc

    changed_at = utcnow()
    for name, old_value, new_value in history:
        session.add(
            FieldChange(
                entity_type=entity_type,
                record_id=record_id_of(record),
                field_name=name,
                # Encoded (UUIDs/datetimes → JSON scalars) so typed values
                # survive the JSON column round-trip.
                old_value=jsonable_encoder(old_value),
                new_value=jsonable_encoder(new_value),
                changed_at=changed_at,
                changed_by=acting_user_id,
            )
        )
    _append_feed_entry(session, entity_type, record, "updated")
    session.flush()
    return record
