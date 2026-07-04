"""The cross-cutting read/write processes (WTK-130): list engine + write engine.

PostalCode is the guinea-pig entity throughout: it is a real registered table
with text columns, and the engines are generic over any entity carrying the
structural columns — nothing here is postal-specific.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mentorapp.api import (
    ApiValidationError,
    DuplicateCandidatesError,
    StaleRowVersionError,
    count_and_aggregates,
    create_record,
    decode_cursor,
    encode_cursor,
    keyset_page,
    normalize_for_match,
    partial_update,
    serialize_record,
    trigram_search_filter,
)
from mentorapp.storage import (
    ChangeFeedEntry,
    DuplicateOverride,
    FieldChange,
    OptionSet,
    OptionValue,
    PostalCode,
    SchemaRegistry,
    utcnow,
)

ENTITY = "postalCode"


def _register(session: Session, field_name: str, **overrides: Any) -> SchemaRegistry:
    kwargs: dict[str, Any] = {"field_type": "text", "field_label": field_name, **overrides}
    row = SchemaRegistry(entity_type=ENTITY, field_name=field_name, **kwargs)
    session.add(row)
    return row


@pytest.fixture()
def registry(session: Session) -> None:
    """Registry rows for postalCode: built-ins, a match rule, and custom fields."""
    option_set = OptionSet(option_set_name="serviceTiers")
    active = OptionValue(
        option_set=option_set, option_value_name="gold", option_value_label="Gold"
    )
    retired = OptionValue(
        option_set=option_set,
        option_value_name="legacy",
        option_value_label="Legacy",
        active_flag=False,
    )
    session.add_all([option_set, active, retired])
    _register(session, "postalCodeValue", required_flag=True)
    _register(
        session,
        "cityName",
        required_flag=True,
        searchable_flag=True,
        history_tracked_flag=True,
        validation_rules={"duplicateMatchRules": ["byCityState"]},
    )
    _register(
        session,
        "stateCode",
        required_flag=True,
        validation_rules={"duplicateMatchRules": ["byCityState"]},
    )
    _register(session, "countryCode")
    _register(session, "regionLabel", user_defined_flag=True, history_tracked_flag=True)
    _register(
        session,
        "serviceTier",
        field_type="choice",
        option_set=option_set,
        user_defined_flag=True,
    )
    session.flush()


def _seed_codes(session: Session, cities: list[tuple[str, str, str]]) -> list[PostalCode]:
    rows = [
        PostalCode(postal_code_value=value, city_name=city, state_code=state)
        for value, city, state in cities
    ]
    session.add_all(rows)
    session.flush()
    return rows


# --- keyset_list_reads -------------------------------------------------------


def test_cursor_round_trips_datetimes_and_plain_values() -> None:
    record_id = uuid.uuid4()
    stamp = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)
    assert decode_cursor(encode_cursor(stamp, record_id)) == (stamp, record_id)
    assert decode_cursor(encode_cursor("Springfield", record_id)) == ("Springfield", record_id)


def test_invalid_cursor_is_a_validation_failure() -> None:
    with pytest.raises(ApiValidationError) as caught:
        decode_cursor("not-a-cursor")
    assert caught.value.errors[0]["fieldName"] == "cursor"


def test_keyset_page_walks_all_rows_once_including_sort_ties(session: Session) -> None:
    # Two Springfields force the record-ID tiebreak; the walk must not skip or
    # repeat either of them across the page boundary.
    _seed_codes(
        session,
        [
            ("62701", "Springfield", "IL"),
            ("65801", "Springfield", "MO"),
            ("60601", "Chicago", "IL"),
            ("53202", "Milwaukee", "WI"),
            ("50309", "Des Moines", "IA"),
        ],
    )
    seen: list[uuid.UUID] = []
    cursor: str | None = None
    pages = 0
    while True:
        rows, cursor = keyset_page(
            session, PostalCode, sort_field="cityName", page_size=2, cursor=cursor
        )
        seen.extend(row.postal_code_id for row in rows)
        pages += 1
        if cursor is None:
            break
    assert pages == 3
    assert len(seen) == len(set(seen)) == 5


def test_keyset_page_excludes_soft_deleted_rows_by_default(session: Session) -> None:
    rows = _seed_codes(session, [("62701", "Springfield", "IL"), ("60601", "Chicago", "IL")])
    rows[0].deleted_at = utcnow()
    session.flush()
    live, cursor = keyset_page(session, PostalCode, sort_field="cityName", page_size=10)
    assert [row.city_name for row in live] == ["Chicago"]
    assert cursor is None
    withdeleted, _ = keyset_page(
        session, PostalCode, sort_field="cityName", page_size=10, include_deleted=True
    )
    assert len(withdeleted) == 2


def test_counts_and_aggregates_span_the_whole_filtered_set(session: Session) -> None:
    _seed_codes(
        session,
        [
            ("62701", "Springfield", "IL"),
            ("60601", "Chicago", "IL"),
            ("53202", "Milwaukee", "WI"),
        ],
    )
    meta = count_and_aggregates(
        session,
        PostalCode,
        filters=[PostalCode.state_code == "IL"],
        aggregates={"maxCityName": func.max(PostalCode.city_name)},
    )
    assert meta == {"totalCount": 2, "maxCityName": "Springfield"}


# --- server_side_trigram_search ----------------------------------------------


def test_search_filters_via_registry_declared_columns(session: Session, registry: None) -> None:
    _seed_codes(session, [("62701", "Springfield", "IL"), ("60601", "Chicago", "IL")])
    predicate = trigram_search_filter(session, PostalCode, ENTITY, "spring")
    rows, _ = keyset_page(
        session, PostalCode, sort_field="cityName", page_size=10, filters=[predicate]
    )
    assert [row.city_name for row in rows] == ["Springfield"]


def test_search_escapes_like_wildcards(session: Session, registry: None) -> None:
    _seed_codes(session, [("62701", "Spring%field", "IL"), ("60601", "Springfield", "IL")])
    predicate = trigram_search_filter(session, PostalCode, ENTITY, "spring%f")
    rows, _ = keyset_page(
        session, PostalCode, sort_field="cityName", page_size=10, filters=[predicate]
    )
    assert [row.city_name for row in rows] == ["Spring%field"]


def test_search_rejects_entities_with_no_searchable_columns(session: Session) -> None:
    with pytest.raises(ApiValidationError):
        trigram_search_filter(session, PostalCode, ENTITY, "spring")


# --- create: validation, duplicates, audit, feed ------------------------------


def _create_springfield(session: Session, **kwargs: Any) -> PostalCode:
    return create_record(
        session,
        PostalCode,
        ENTITY,
        {"postalCodeValue": "62701", "cityName": "Springfield", "stateCode": "IL"},
        **kwargs,
    )


def test_create_stamps_audit_merges_custom_and_feeds(session: Session, registry: None) -> None:
    actor = uuid.uuid4()
    record = create_record(
        session,
        PostalCode,
        ENTITY,
        {
            "postalCodeValue": "62701",
            "cityName": "Springfield",
            "stateCode": "IL",
            "regionLabel": "Central",
        },
        acting_user_id=actor,
    )
    assert record.created_by == actor
    assert record.custom_attributes == {"regionLabel": "Central"}
    served = serialize_record(record)
    assert served["regionLabel"] == "Central"
    assert "customAttributes" not in served
    feed = session.scalars(select(ChangeFeedEntry)).all()
    assert [(entry.change_kind, entry.record_row_version) for entry in feed] == [("created", 1)]


def test_create_reports_every_failure_in_one_round_trip(
    session: Session, registry: None
) -> None:
    retired_id = (
        session.scalars(select(OptionValue).where(OptionValue.active_flag.is_(False)))
        .one()
        .option_value_id
    )
    with pytest.raises(ApiValidationError) as caught:
        create_record(
            session,
            PostalCode,
            ENTITY,
            {
                "cityName": 42,  # typeMismatch
                "bogusField": "x",  # unknownField
                "rowVersion": 7,  # readOnlyField
                "serviceTier": str(retired_id),  # inactiveOption
                # postalCodeValue and stateCode missing → requiredField x2
            },
        )
    codes = sorted(error["code"] for error in caught.value.errors)
    assert codes == [
        "inactiveOption",
        "readOnlyField",
        "requiredField",
        "requiredField",
        "typeMismatch",
        "unknownField",
    ]


def test_duplicate_create_rejects_with_candidates_then_override_is_recorded(
    session: Session, registry: None
) -> None:
    original = _create_springfield(session)
    with pytest.raises(DuplicateCandidatesError) as caught:
        create_record(
            session,
            PostalCode,
            ENTITY,
            # Case/whitespace variants must still match: one normalizer (DB-S13).
            {"postalCodeValue": "62702", "cityName": "  SPRINGFIELD ", "stateCode": "il"},
        )
    assert caught.value.candidates[0]["postalCodeID"] == original.postal_code_id
    override = create_record(
        session,
        PostalCode,
        ENTITY,
        {"postalCodeValue": "62702", "cityName": "Springfield", "stateCode": "IL"},
        override_duplicates=True,
        override_reason="distinct carrier route",
    )
    recorded = session.scalars(select(DuplicateOverride)).one()
    assert recorded.record_id == override.postal_code_id
    assert recorded.matched_rule_names == ["byCityState"]
    assert recorded.candidate_record_ids == [str(original.postal_code_id)]


# --- partial update: OCC, history, no-op --------------------------------------


def test_stale_row_version_raises_with_current_record(session: Session, registry: None) -> None:
    record = _create_springfield(session)
    with pytest.raises(StaleRowVersionError) as caught:
        partial_update(session, record, ENTITY, {"cityName": "Springfield II"}, row_version=99)
    assert caught.value.current_record["rowVersion"] == record.row_version


def test_patch_applies_changes_writes_history_and_feeds(
    session: Session, registry: None
) -> None:
    actor = uuid.uuid4()
    record = _create_springfield(session)
    partial_update(
        session,
        record,
        ENTITY,
        {"cityName": "New Springfield", "regionLabel": "Central"},
        row_version=1,
        acting_user_id=actor,
    )
    assert record.row_version == 2
    assert record.modified_by == actor
    assert record.custom_attributes == {"regionLabel": "Central"}
    changes = session.scalars(select(FieldChange).order_by(FieldChange.field_name)).all()
    assert [(c.field_name, c.old_value, c.new_value) for c in changes] == [
        ("cityName", "Springfield", "New Springfield"),
        ("regionLabel", None, "Central"),
    ]
    kinds = session.scalars(select(ChangeFeedEntry.change_kind)).all()
    assert kinds == ["created", "updated"]


def test_patch_with_unchanged_values_is_a_no_op(session: Session, registry: None) -> None:
    record = _create_springfield(session)
    partial_update(session, record, ENTITY, {"cityName": "Springfield"}, row_version=1)
    assert record.row_version == 1
    assert session.scalars(select(FieldChange)).all() == []
    kinds = session.scalars(select(ChangeFeedEntry.change_kind)).all()
    assert kinds == ["created"]


def test_phone_normalization_is_digits_only() -> None:
    assert normalize_for_match("phone", "(217) 555-0134") == "2175550134"
    assert normalize_for_match("email", "  Doug@CBM.org ") == "doug@cbm.org"
