"""Write/read process guarantees (WTK-146): the contract edges WTK-130's suite
asserts only implicitly.

REQ-059 (PATCH sends only changes): unsent fields and unsent custom-attribute
keys are provably untouched, and validation failure leaves the record pristine.
REQ-049 (custom attributes): the bag merges key-level and serves flat.
REQ-055 (list reads): the keyset walk neither repeats nor skips under
concurrent inserts, counts span the whole filtered set independent of any
cursor, and search matches ONLY registry-declared searchable columns.
DB-S3 x duplicate detection: a soft-deleted corpse never blocks a live create.

PostalCode is the guinea-pig entity, as in test_api_processes — the engines
are generic; nothing here is postal-specific.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.api import (
    ApiValidationError,
    count_and_aggregates,
    create_record,
    keyset_page,
    partial_update,
    serialize_record,
    trigram_search_filter,
)
from mentorapp.storage import (
    DuplicateOverride,
    FieldChange,
    PostalCode,
    SchemaRegistry,
    utcnow,
)

ENTITY = "postalCode"


def _register(session: Session, field_name: str, **overrides: Any) -> None:
    kwargs: dict[str, Any] = {"field_type": "text", "field_label": field_name, **overrides}
    session.add(SchemaRegistry(entity_type=ENTITY, field_name=field_name, **kwargs))


@pytest.fixture()
def registry(session: Session) -> None:
    """postalCode registry: only cityName is searchable; two custom fields with
    opposite history flags so history gating is observable per key."""
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
    _register(session, "districtLabel", user_defined_flag=True)
    session.flush()


def _create_springfield(session: Session, **custom: Any) -> PostalCode:
    return create_record(
        session,
        PostalCode,
        ENTITY,
        {"postalCodeValue": "62701", "cityName": "Springfield", "stateCode": "IL", **custom},
    )


def _seed_codes(session: Session, cities: list[tuple[str, str, str]]) -> list[PostalCode]:
    rows = [
        PostalCode(postal_code_value=value, city_name=city, state_code=state)
        for value, city, state in cities
    ]
    session.add_all(rows)
    session.flush()
    return rows


# --- REQ-059: partial update touches only sent fields --------------------------


def test_patch_leaves_unsent_fields_and_custom_keys_untouched(
    session: Session, registry: None
) -> None:
    record = _create_springfield(session, regionLabel="Central", districtLabel="D1")
    partial_update(session, record, ENTITY, {"cityName": "New Springfield"}, row_version=1)
    assert record.row_version == 2
    assert (record.postal_code_value, record.state_code) == ("62701", "IL")
    # The bag survives whole: a built-in-only PATCH never rewrites custom keys.
    assert record.custom_attributes == {"regionLabel": "Central", "districtLabel": "D1"}
    changed = session.scalars(select(FieldChange.field_name)).all()
    assert changed == ["cityName"]


def test_custom_attribute_patch_merges_key_level_and_serves_flat(
    session: Session, registry: None
) -> None:
    record = _create_springfield(session, regionLabel="Central", districtLabel="D1")
    partial_update(session, record, ENTITY, {"regionLabel": "North"}, row_version=1)
    assert record.custom_attributes == {"regionLabel": "North", "districtLabel": "D1"}
    assert record.city_name == "Springfield"
    served = serialize_record(record)
    assert (served["regionLabel"], served["districtLabel"]) == ("North", "D1")
    assert "customAttributes" not in served
    # regionLabel is history-tracked; the untouched districtLabel writes nothing.
    changes = session.scalars(select(FieldChange)).all()
    assert [(c.field_name, c.old_value, c.new_value) for c in changes] == [
        ("regionLabel", "Central", "North")
    ]


def test_rejected_patch_leaves_the_record_pristine(session: Session, registry: None) -> None:
    record = _create_springfield(session, regionLabel="Central")
    with pytest.raises(ApiValidationError) as caught:
        partial_update(
            session,
            record,
            ENTITY,
            {"regionLabel": 42, "bogusField": "x", "cityName": "Sprungfeld"},
            row_version=1,
        )
    assert sorted(error["code"] for error in caught.value.errors) == [
        "typeMismatch",
        "unknownField",
    ]
    # All-or-nothing: the valid cityName change must not land beside the failures.
    assert record.city_name == "Springfield"
    assert record.row_version == 1
    assert record.custom_attributes == {"regionLabel": "Central"}


# --- REQ-059: duplicate override recording; DB-S3 corpse exemption -------------


def test_override_records_reason_and_actor(session: Session, registry: None) -> None:
    original = _create_springfield(session)
    actor = uuid.uuid4()
    created = create_record(
        session,
        PostalCode,
        ENTITY,
        {"postalCodeValue": "62702", "cityName": "Springfield", "stateCode": "IL"},
        acting_user_id=actor,
        override_duplicates=True,
        override_reason="distinct carrier route",
    )
    recorded = session.scalars(select(DuplicateOverride)).one()
    assert recorded.record_id == created.postal_code_id
    assert recorded.candidate_record_ids == [str(original.postal_code_id)]
    assert recorded.override_reason == "distinct carrier route"
    assert recorded.created_by == actor


def test_soft_deleted_records_never_trip_duplicate_detection(
    session: Session, registry: None
) -> None:
    corpse = _create_springfield(session)
    corpse.deleted_at = utcnow()
    session.flush()
    revived = create_record(
        session,
        PostalCode,
        ENTITY,
        {"postalCodeValue": "62702", "cityName": "Springfield", "stateCode": "IL"},
    )
    assert revived.deleted_at is None
    # No candidates matched, so nothing to override and nothing recorded.
    assert session.scalars(select(DuplicateOverride)).all() == []


# --- REQ-055: keyset stability, cursor-free counts, searchable-only search -----


def test_keyset_walk_neither_repeats_nor_skips_under_concurrent_inserts(
    session: Session,
) -> None:
    _seed_codes(
        session,
        [
            ("60601", "Chicago", "IL"),
            ("50309", "Des Moines", "IA"),
            ("53202", "Milwaukee", "WI"),
            ("62701", "Springfield", "IL"),
        ],
    )
    first, cursor = keyset_page(session, PostalCode, sort_field="cityName", page_size=2)
    assert [row.city_name for row in first] == ["Chicago", "Des Moines"]
    assert cursor is not None
    # "Concurrent" inserts land mid-walk: one before the cursor position, one
    # after. The already-served pages must not shift under the reader.
    _seed_codes(session, [("60505", "Aurora", "IL"), ("61602", "Peoria", "IL")])
    rest: list[str] = []
    while cursor is not None:
        rows, cursor = keyset_page(
            session, PostalCode, sort_field="cityName", page_size=2, cursor=cursor
        )
        rest.extend(row.city_name for row in rows)
    # Peoria (after the cursor) is served; Aurora (before it) belongs to the
    # NEXT walk — and nothing already served ever comes back.
    assert rest == ["Milwaukee", "Peoria", "Springfield"]


def test_counts_span_the_filtered_set_regardless_of_walk_position(session: Session) -> None:
    rows = _seed_codes(
        session,
        [
            ("60601", "Chicago", "IL"),
            ("50309", "Des Moines", "IA"),
            ("53202", "Milwaukee", "WI"),
            ("62701", "Springfield", "IL"),
        ],
    )
    rows[2].deleted_at = utcnow()
    session.flush()
    _, cursor = keyset_page(session, PostalCode, sort_field="cityName", page_size=1)
    assert cursor is not None
    # The count query takes filters, never a cursor: mid-walk it still covers
    # the whole live set, and the soft-deleted row is already outside it.
    assert count_and_aggregates(session, PostalCode) == {"totalCount": 3}


def test_search_never_matches_non_searchable_columns(session: Session, registry: None) -> None:
    # "spring" appears in the first row's postalCodeValue, but only cityName
    # carries searchableFlag — so only the Springfield row may hit.
    _seed_codes(session, [("Spring1", "Chicago", "IL"), ("62701", "Springfield", "IL")])
    predicate = trigram_search_filter(session, PostalCode, ENTITY, "spring")
    rows, _ = keyset_page(
        session, PostalCode, sort_field="cityName", page_size=10, filters=[predicate]
    )
    assert [row.city_name for row in rows] == ["Springfield"]
