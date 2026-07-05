"""The grid server API surface design (WTK-042): REQ-020/023/026/027/028.

What the WTK-047/050/051 builds rely on, proven here at design level:

- REQ-020: search arms only at three characters, scopes to displayed ∩
  searchable columns (an unsearchable displayed set fails loudly), layers on
  top of the view's filters, and the recent list caps at five per grid
  through the one preference mechanism.
- REQ-023: the selection wire shape round-trips opaque data-source string
  identifiers (FND-020) with bounded validation, select-all means the whole
  filtered set minus exclusions, and hidden-selection counting/wording keeps
  a filtered-away selection honest.
- REQ-026: footer and group aggregates span the ENTIRE filtered set with the
  central deleted-row exclusion; bad specs report every failure at once.
- REQ-027: the selection-else-filtered rule, format gating, and the job
  payload carrying the view rendering — the full directional multi-key sort
  included (FND-021) — + the artifact_jobs job types.
- REQ-028: the four-way deep-link decision — links are references, never
  grants — with the fallback consuming the last-view preference (FND-018).

PostalCode is the guinea-pig entity, as in test_api_process_guarantees —
the surface is generic; nothing here is postal-specific. The selection-seam
SQL tests use :class:`CrmRosterRecord` instead: a data-source-shaped stand-in
whose primary key is an opaque STRING, because that is exactly the identifier
shape FND-020 reconciles the surface to.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import String
from sqlalchemy.orm import Mapped, Session, mapped_column

from mentorapp.api import (
    GRID_SURFACE,
    AggregateSpec,
    ApiValidationError,
    ExplicitSelection,
    FallbackToLastUsed,
    FilteredSetSelection,
    GridDocumentRequest,
    LinkAccessDenied,
    OpenLinkedView,
    SortKey,
    aggregate_expressions,
    count_and_aggregates,
    export_job_payload,
    grid_search_filter,
    group_row_aggregates,
    hidden_rows_confirmation,
    hidden_selection_count,
    keyset_page,
    last_view_preference_key,
    parse_selection,
    print_job_payload,
    recent_searches_key,
    remember_search,
    resolve_export_scope,
    resolve_grid_link,
    selection_record_filter,
)
from mentorapp.api.grid_surface import (
    GRID_AGGREGATES,
    GRID_EXPORT,
    GRID_LINK_RESOLUTION,
    GRID_PRINT,
    GRID_ROWS,
    RECORD_ID_MAX_LENGTH,
    ExportScope,
    GridLink,
)
from mentorapp.api.list_engine import CODE_SEARCH_NOT_SUPPORTED
from mentorapp.automation.artifact_jobs import EXPORT_JOB_TYPE, PRINT_JOB_TYPE
from mentorapp.storage import Base, PostalCode, SchemaRegistry, StructuralColumnsMixin, utcnow

ENTITY = "postalCode"

# A CRM-style record identifier: 17 hex-ish characters, NOT a UUID (FND-020).
CRM_ID = "68a1b2c3d4e5f6a7b"


class CrmRosterRecord(StructuralColumnsMixin, Base):
    """Selection-seam stand-in: a data source whose record IDs are opaque strings.

    FND-020's whole point — the selection predicates must compose against a
    key column that stores data-source identifiers verbatim, never UUIDs.
    """

    __tablename__ = "CrmRosterRecord"

    record_id: Mapped[str] = mapped_column("recordID", String(200), primary_key=True)
    city_name: Mapped[str] = mapped_column("cityName", String(100), nullable=False)


@pytest.fixture()
def registry(session: Session) -> None:
    """cityName is the only searchable column — the intersection is observable."""
    for field_name, searchable in [
        ("postalCodeValue", False),
        ("cityName", True),
        ("stateCode", False),
    ]:
        session.add(
            SchemaRegistry(
                entity_type=ENTITY,
                field_name=field_name,
                field_type="text",
                field_label=field_name,
                searchable_flag=searchable,
            )
        )
    session.flush()


def _rows(session: Session, *specs: tuple[str, str, str]) -> list[PostalCode]:
    made = [
        PostalCode(postal_code_value=value, city_name=city, state_code=state)
        for value, city, state in specs
    ]
    session.add_all(made)
    session.flush()
    return made


def _crm_rows(session: Session, *specs: tuple[str, str]) -> list[CrmRosterRecord]:
    made = [CrmRosterRecord(record_id=rid, city_name=city) for rid, city in specs]
    session.add_all(made)
    session.flush()
    return made


# --- The endpoint contracts --------------------------------------------------------


def test_surface_declares_five_distinct_endpoints() -> None:
    assert len({(c.method, c.path) for c in GRID_SURFACE}) == 5
    assert GRID_ROWS in GRID_SURFACE and GRID_LINK_RESOLUTION in GRID_SURFACE


def test_only_export_and_print_carry_the_over_ten_seconds_declaration() -> None:
    # The DB-S11 judgment is declared in the contract, not discovered live:
    # document producers enqueue; reads answer inline.
    assert {c.path for c in GRID_SURFACE if c.over_ten_seconds} == {
        GRID_EXPORT.path,
        GRID_PRINT.path,
    }
    assert GRID_AGGREGATES.over_ten_seconds is False


# --- REQ-020: live search ----------------------------------------------------------


def test_search_below_three_characters_is_no_filter(session: Session, registry: None) -> None:
    assert (
        grid_search_filter(session, PostalCode, ENTITY, "  sp ", displayed_fields=["cityName"])
        is None
    )


def test_search_scopes_to_displayed_searchable_columns(
    session: Session, registry: None
) -> None:
    _rows(
        session,
        ("62701", "Springfield", "IL"),
        ("97477", "Springfield", "OR"),
        ("10001", "New York", "NY"),
    )
    predicate = grid_search_filter(
        session, PostalCode, ENTITY, "spring", displayed_fields=["cityName", "stateCode"]
    )
    assert predicate is not None
    rows, _ = keyset_page(
        session, PostalCode, sort_field="postalCodeValue", page_size=10, filters=[predicate]
    )
    assert {row.city_name for row in rows} == {"Springfield"}


def test_search_layers_on_top_of_view_filters(session: Session, registry: None) -> None:
    # REQ-020: search filters on top of the view's own filters, never replaces
    # them — an Oregon-only view searched for "spring" shows only Oregon.
    _rows(session, ("62701", "Springfield", "IL"), ("97477", "Springfield", "OR"))
    predicate = grid_search_filter(
        session, PostalCode, ENTITY, "spring", displayed_fields=["cityName"]
    )
    assert predicate is not None
    rows, _ = keyset_page(
        session,
        PostalCode,
        sort_field="postalCodeValue",
        page_size=10,
        filters=[PostalCode.state_code == "OR", predicate],
    )
    assert [(row.city_name, row.state_code) for row in rows] == [("Springfield", "OR")]


def test_search_over_unsearchable_displayed_columns_fails_loudly(
    session: Session, registry: None
) -> None:
    with pytest.raises(ApiValidationError) as failure:
        grid_search_filter(
            session, PostalCode, ENTITY, "spring", displayed_fields=["stateCode"]
        )
    assert failure.value.errors[0]["code"] == CODE_SEARCH_NOT_SUPPORTED


def test_preference_keys_are_the_single_home_for_per_user_grid_state() -> None:
    # FND-017/FND-018 (DB-S13): BOTH durable per-user pieces — the remembered
    # searches and the last-used view — ride userPreference keys defined here,
    # and nowhere else (storage tables no per-user grid state).
    assert recent_searches_key("mentorRoster") == "grid.mentorRoster.recentSearches"
    assert last_view_preference_key("mentorRoster") == "grid.mentorRoster.lastView"


def test_recent_searches_cap_dedupe_and_key() -> None:
    assert recent_searches_key("mentorRoster") == "grid.mentorRoster.recentSearches"
    history: list[str] = []
    for needle in ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]:
        history = remember_search(history, needle)
    assert history == ["zeta", "epsilon", "delta", "gamma", "beta"]
    # A repeat moves to the front; sub-minimum text never ran, so never lands.
    assert remember_search(history, "gamma")[0] == "gamma"
    assert len(remember_search(history, "gamma")) == 5
    assert remember_search(history, "zz") == history


# --- REQ-026: aggregates over the entire filtered set -------------------------------


def test_aggregate_expressions_report_every_bad_spec_at_once() -> None:
    with pytest.raises(ApiValidationError) as failure:
        aggregate_expressions(
            PostalCode,
            [
                AggregateSpec("median", "cityName"),
                AggregateSpec("max", "noSuchField"),
                AggregateSpec("min", "cityName"),
            ],
        )
    assert [e["code"] for e in failure.value.errors] == [
        "unknownAggregateFunction",
        "unknownAggregateField",
    ]


def test_footer_aggregates_span_whole_filtered_set(session: Session) -> None:
    live = _rows(
        session,
        ("62701", "Springfield", "IL"),
        ("60601", "Chicago", "IL"),
        ("97477", "Springfield", "OR"),
    )
    live[2].deleted_at = utcnow()  # excluded centrally, never per endpoint
    session.flush()
    result = count_and_aggregates(
        session,
        PostalCode,
        aggregates=aggregate_expressions(PostalCode, [AggregateSpec("max", "cityName")]),
    )
    assert result == {"totalCount": 2, "max:cityName": "Springfield"}


def test_group_row_aggregates_group_filter_and_exclude_deleted(session: Session) -> None:
    made = _rows(
        session,
        ("62701", "Springfield", "IL"),
        ("60601", "Chicago", "IL"),
        ("97477", "Springfield", "OR"),
        ("10001", "New York", "NY"),
    )
    made[3].deleted_at = utcnow()
    session.flush()
    groups = group_row_aggregates(
        session,
        PostalCode,
        group_by_field="stateCode",
        specs=[AggregateSpec("min", "cityName")],
        filters=[PostalCode.city_name != "Chicago"],
    )
    assert groups == [
        {"groupValue": "IL", "totalCount": 1, "min:cityName": "Springfield"},
        {"groupValue": "OR", "totalCount": 1, "min:cityName": "Springfield"},
    ]


def test_group_rows_reject_unknown_group_field(session: Session) -> None:
    with pytest.raises(ApiValidationError) as failure:
        group_row_aggregates(session, PostalCode, group_by_field="noSuchField")
    assert failure.value.errors[0]["code"] == "unknownAggregateField"


# --- REQ-023: selection scope --------------------------------------------------------


def test_selection_wire_shape_round_trips_opaque_string_ids() -> None:
    # FND-020: a CRM-style identifier is accepted verbatim — never parsed as
    # a UUID, never normalized.
    explicit = parse_selection({"selectionKind": "explicit", "recordIds": [CRM_ID]})
    assert explicit == ExplicitSelection((CRM_ID,))
    select_all = parse_selection({"selectionKind": "filteredSet"})
    assert select_all == FilteredSetSelection(())


@pytest.mark.parametrize(
    "payload",
    [
        {"selectionKind": "everything"},
        {},
        # FND-020's bounds: non-empty, ≤200 characters, no control characters,
        # and a string in the first place.
        {"selectionKind": "explicit", "recordIds": [""]},
        {"selectionKind": "explicit", "recordIds": ["x" * (RECORD_ID_MAX_LENGTH + 1)]},
        {"selectionKind": "explicit", "recordIds": ["rec\x00one"]},
        {"selectionKind": "filteredSet", "excludedRecordIds": [42]},
    ],
)
def test_malformed_selection_fails_per_field(payload: dict[str, Any]) -> None:
    with pytest.raises(ApiValidationError) as failure:
        parse_selection(payload)
    assert failure.value.errors[0]["code"] == "invalidSelection"


def test_selection_filters_compose_with_the_grid_filters(session: Session) -> None:
    # The stand-in's key column stores opaque CRM strings (FND-020) — the
    # predicates compose against exactly the identifier shape the wire carries.
    made = _crm_rows(
        session,
        ("68a1b2c3d4e5f6a7b", "Springfield"),
        ("68a1b2c3d4e5f6a7c", "Chicago"),
        ("68a1b2c3d4e5f6a7d", "Springfield"),
    )
    explicit = selection_record_filter(ExplicitSelection((made[0].record_id,)), CrmRosterRecord)
    assert explicit is not None
    rows, _ = keyset_page(
        session, CrmRosterRecord, sort_field="recordID", page_size=10, filters=[explicit]
    )
    assert [row.record_id for row in rows] == [made[0].record_id]
    # Select-all with nothing deselected adds NO predicate — it IS the filtered set.
    assert selection_record_filter(FilteredSetSelection(()), CrmRosterRecord) is None
    minus_one = selection_record_filter(
        FilteredSetSelection((made[1].record_id,)), CrmRosterRecord
    )
    assert minus_one is not None
    rows, _ = keyset_page(
        session, CrmRosterRecord, sort_field="recordID", page_size=10, filters=[minus_one]
    )
    assert made[1].record_id not in {row.record_id for row in rows}
    assert len(rows) == 2


def test_hidden_selection_count_and_confirmation_wording(session: Session) -> None:
    made = _crm_rows(
        session,
        ("68a1b2c3d4e5f6a7b", "Springfield"),
        ("68a1b2c3d4e5f6a7c", "Chicago"),
        ("68a1b2c3d4e5f6a7d", "Springfield"),
    )
    selected = [row.record_id for row in made]
    hidden = hidden_selection_count(
        session,
        CrmRosterRecord,
        selected,
        filters=[CrmRosterRecord.city_name == "Springfield"],
    )
    assert hidden == 1  # the Chicago row stays selected but filtered out
    assert hidden_selection_count(session, CrmRosterRecord, [], filters=[]) == 0
    assert hidden_rows_confirmation(0, "Export") is None
    wording = hidden_rows_confirmation(hidden, "Export")
    assert wording is not None and "1 selected row" in wording and "hiding" in wording


# --- REQ-027: export & print ---------------------------------------------------------


def test_selection_else_filtered_rule() -> None:
    assert resolve_export_scope(ExplicitSelection((CRM_ID,))) == ExportScope(
        "selection", record_ids=(CRM_ID,)
    )
    # No selection — or an empty one — exports the ENTIRE filtered set.
    assert resolve_export_scope(None).scope_kind == "filteredSet"
    assert resolve_export_scope(ExplicitSelection(())).scope_kind == "filteredSet"
    unpicked = "68a1b2c3d4e5f6a7c"
    assert resolve_export_scope(FilteredSetSelection((unpicked,))) == ExportScope(
        "filteredSet", excluded_record_ids=(unpicked,)
    )


def _document_request(**overrides: Any) -> GridDocumentRequest:
    kwargs: dict[str, Any] = {
        "entity_type": ENTITY,
        "columns": ("cityName", "stateCode"),
        "sort_keys": (SortKey("cityName", "asc"),),
        "filter_state": {"stateCode": "IL", "search": "spring"},
        "scope": resolve_export_scope(None),
        **overrides,
    }
    return GridDocumentRequest(**kwargs)


def test_export_payload_carries_the_view_rendering() -> None:
    job_type, payload = export_job_payload(
        _document_request(export_format="excel", raw_values=True)
    )
    assert job_type == EXPORT_JOB_TYPE
    assert payload == {
        "entityType": ENTITY,
        "columns": ["cityName", "stateCode"],  # display order preserved
        "sortKeys": [{"field": "cityName", "direction": "asc"}],
        "filterState": {"stateCode": "IL", "search": "spring"},
        "scope": {"scopeKind": "filteredSet"},
        "rawValues": True,
        "exportFormat": "excel",
    }


def test_export_reproduces_a_three_key_descending_sort() -> None:
    # FND-021: the artifact reproduces the grid's FULL directional sort —
    # every key, direction included, priority = order (1-based position, the
    # same semantics as storage sortSpec and the UI's header-sort model).
    keys = (
        SortKey("stateCode", "desc"),
        SortKey("cityName", "desc"),
        SortKey("postalCodeValue", "desc"),
    )
    _, payload = export_job_payload(_document_request(sort_keys=keys))
    assert payload["sortKeys"] == [
        {"field": "stateCode", "direction": "desc"},
        {"field": "cityName", "direction": "desc"},
        {"field": "postalCodeValue", "direction": "desc"},
    ]


def test_document_sort_rejects_a_fourth_key_and_unknown_directions() -> None:
    four = tuple(SortKey(f"field{n}", "asc") for n in range(4))
    with pytest.raises(ApiValidationError) as failure:
        export_job_payload(_document_request(sort_keys=four))
    assert failure.value.errors[0]["code"] == "invalidSort"
    # Print rides the same gate — the wire vocabulary is asc/desc, nothing else.
    with pytest.raises(ApiValidationError) as failure:
        print_job_payload(_document_request(sort_keys=(SortKey("cityName", "down"),)))
    assert failure.value.errors[0]["code"] == "invalidSort"


def test_export_defaults_formatted_and_rejects_unknown_format() -> None:
    _, payload = export_job_payload(_document_request())
    assert payload["rawValues"] is False and payload["exportFormat"] == "csv"
    with pytest.raises(ApiValidationError) as failure:
        export_job_payload(_document_request(export_format="pdf"))
    assert failure.value.errors[0]["code"] == "unsupportedExportFormat"


def test_print_payload_shares_the_scope_contract_without_format() -> None:
    job_type, payload = print_job_payload(
        _document_request(scope=resolve_export_scope(ExplicitSelection((CRM_ID,))))
    )
    assert job_type == PRINT_JOB_TYPE
    assert payload["scope"] == {"scopeKind": "selection", "recordIds": [CRM_ID]}
    assert "exportFormat" not in payload


# --- REQ-028: deep-link resolution ---------------------------------------------------


def _link(owner: uuid.UUID | None) -> GridLink:
    return GridLink(grid_id="mentorRoster", view_id=uuid.uuid4(), view_owner_id=owner)


def test_link_without_data_source_access_is_denied() -> None:
    outcome = resolve_grid_link(
        _link(None),
        requester_id=uuid.uuid4(),
        has_data_source_access=False,
        last_view_preference=uuid.uuid4(),
    )
    assert isinstance(outcome, LinkAccessDenied)
    assert "grant" in outcome.notice


def test_system_view_and_own_view_open_as_named() -> None:
    me = uuid.uuid4()
    system_link = _link(None)
    own_link = _link(me)
    for link in (system_link, own_link):
        outcome = resolve_grid_link(
            link, requester_id=me, has_data_source_access=True, last_view_preference=None
        )
        assert outcome == OpenLinkedView(link.view_id)


def test_another_users_private_view_falls_back_to_the_preference() -> None:
    # FND-018: the fallback target is the requester's last-view PREFERENCE
    # (userPreference key `grid.{gridId}.lastView`), read by the endpoint.
    last_used = uuid.uuid4()
    outcome = resolve_grid_link(
        _link(uuid.uuid4()),
        requester_id=uuid.uuid4(),
        has_data_source_access=True,
        last_view_preference=last_used,
    )
    assert isinstance(outcome, FallbackToLastUsed)
    assert outcome.view_id == last_used and "last-used" in outcome.notice
    # Preference unset — or stale, naming a vanished view (the endpoint passes
    # None for both): the grid still opens on its default view, never blank.
    fresh = resolve_grid_link(
        _link(uuid.uuid4()),
        requester_id=uuid.uuid4(),
        has_data_source_access=True,
        last_view_preference=None,
    )
    assert isinstance(fresh, FallbackToLastUsed) and fresh.view_id is None
