"""``/grids`` — rows, aggregates, export, print endpoints (WTK-047).

REQ-020: live search arms at three characters, layers on the view's own
filters over displayed columns only, and the executed search lands in the
per-user recall preference (FND-017). REQ-026: totalCount + declared
aggregates + group rows span the ENTIRE filtered set, never one page.
REQ-027: export/print enqueue artifact jobs carrying the view rendering
(display-order columns, the full directional sort) and the
selection-else-filtered scope, answering the jobID (DB-S11).

PostalCode is the guinea-pig entity as in test_api_grid_surface — the
surface is generic; nothing here is postal-specific.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.routers.grids import get_grid_entity_catalog
from mentorapp.automation.artifact_jobs import (
    EXPORT_JOB_TYPE,
    PRINT_JOB_TYPE,
    export_job_handler,
)
from mentorapp.automation.worker import process_next_job
from mentorapp.main import create_app
from mentorapp.storage import (
    AppUser,
    BackgroundJob,
    ChangeFeedEntry,
    DataSource,
    Grid,
    GridView,
    Notification,
    PostalCode,
    SchemaRegistry,
    SortSpec,
    UserPreference,
    regenerate_read_views,
    uuid7,
)

ENTITY = "postalCode"
GRID_KEY = "postalDirectory"
USER_ID = uuid7()


class _Catalog:
    """Test catalog: the one entity-backed source resolves to PostalCode."""

    def entity_for(self, data_source_key: str) -> tuple[str, type[Any]] | None:
        return (ENTITY, PostalCode) if data_source_key == "postalCodes" else None


@pytest.fixture()
def view_id(session: Session) -> uuid.UUID:
    """One grid + system view over PostalCode: OR-only filter, city search."""
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
    source = DataSource(
        data_source_key="postalCodes",
        data_source_name="Postal codes",
        data_source_sql='SELECT * FROM "vw_postalCode"',
        exposed_fields=["postalCodeValue", "cityName", "stateCode"],
    )
    grid = Grid(grid_key=GRID_KEY, grid_name="Postal directory")
    session.add_all([source, grid])
    session.flush()
    view = GridView(
        grid_id=grid.grid_id,
        data_source_id=source.data_source_id,
        grid_view_name="Oregon",
        view_type="system",
        displayed_fields=[
            {"fieldName": "cityName", "columnWidth": 120, "columnFormat": None},
            {"fieldName": "postalCodeValue", "columnWidth": 80, "columnFormat": None},
        ],
        view_filters={"stateCode": "OR"},
        view_aggregates=[{"function": "count", "fieldName": "postalCodeValue"}],
        grouping_config={"groupFields": ["cityName"]},
    )
    session.add(view)
    session.flush()
    session.add_all(
        [
            SortSpec(
                grid_view_id=view.grid_view_id,
                sort_field_name="cityName",
                sort_direction="ascending",
                sort_position=1,
            ),
            SortSpec(
                grid_view_id=view.grid_view_id,
                sort_field_name="postalCodeValue",
                sort_direction="descending",
                sort_position=2,
            ),
        ]
    )
    session.add_all(
        [
            PostalCode(postal_code_value="97035", city_name="Lake Oswego", state_code="OR"),
            PostalCode(postal_code_value="97401", city_name="Eugene", state_code="OR"),
            PostalCode(postal_code_value="97201", city_name="Portland", state_code="OR"),
            PostalCode(postal_code_value="99201", city_name="Spokane", state_code="WA"),
        ]
    )
    session.commit()
    return view.grid_view_id


@pytest.fixture()
def client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_grid_entity_catalog] = _Catalog
    return TestClient(app)


def _rows(client: TestClient, view_id: uuid.UUID, **params: Any) -> dict[str, Any]:
    response = client.get(f"/grids/{GRID_KEY}/rows", params={"view_id": str(view_id), **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_rows_apply_view_filters_and_primary_sort(
    client: TestClient, view_id: uuid.UUID
) -> None:
    body = _rows(client, view_id)
    assert [r["cityName"] for r in body["data"]] == ["Eugene", "Lake Oswego", "Portland"]
    assert body["meta"]["cursor"] is None
    assert body["meta"]["searchApplied"] is False
    assert body["errors"] is None


def test_rows_page_out_with_a_cursor(client: TestClient, view_id: uuid.UUID) -> None:
    first = _rows(client, view_id, page_size=2)
    assert len(first["data"]) == 2
    assert first["meta"]["cursor"]
    rest = _rows(client, view_id, page_size=2, cursor=first["meta"]["cursor"])
    assert [r["cityName"] for r in rest["data"]] == ["Portland"]


def test_search_layers_on_view_filters_and_is_remembered(
    client: TestClient, session: Session, view_id: uuid.UUID
) -> None:
    # "Spokane" would match "an" too if search replaced the OR filter — it must not.
    body = _rows(client, view_id, search="lan")
    assert [r["cityName"] for r in body["data"]] == ["Portland"]
    assert body["meta"]["searchApplied"] is True
    assert body["meta"]["recentSearches"] == ["lan"]
    row = session.scalars(
        select(UserPreference).where(
            UserPreference.preference_key == f"grid.{GRID_KEY}.recentSearches"
        )
    ).one()
    assert row.user_id == USER_ID
    assert row.preference_value == {"recentSearches": ["lan"]}


def test_short_search_neither_filters_nor_persists(
    client: TestClient, session: Session, view_id: uuid.UUID
) -> None:
    body = _rows(client, view_id, search="la")
    assert len(body["data"]) == 3
    assert body["meta"]["searchApplied"] is False
    assert body["meta"]["recentSearches"] == []
    assert session.scalars(select(UserPreference)).first() is None


def test_recent_searches_stack_most_recent_first(
    client: TestClient, view_id: uuid.UUID
) -> None:
    _rows(client, view_id, search="lan")
    body = _rows(client, view_id, search="eug")
    assert body["meta"]["recentSearches"] == ["eug", "lan"]


def test_aggregates_span_the_whole_filtered_set(client: TestClient, view_id: uuid.UUID) -> None:
    response = client.get(f"/grids/{GRID_KEY}/aggregates", params={"view_id": str(view_id)})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    # Never cursor-bounded: 3 live OR rows whatever the page size was.
    assert data["totalCount"] == 3
    assert data["aggregates"] == {"count:postalCodeValue": 3}
    assert [g["groupValue"] for g in data["groupRows"]] == [
        "Eugene",
        "Lake Oswego",
        "Portland",
    ]
    assert all(g["totalCount"] == 1 for g in data["groupRows"])


def test_aggregates_respect_the_live_search(client: TestClient, view_id: uuid.UUID) -> None:
    data = client.get(
        f"/grids/{GRID_KEY}/aggregates",
        params={"view_id": str(view_id), "search": "lan"},
    ).json()["data"]
    assert data["totalCount"] == 1


def test_export_enqueues_the_view_rendered_job(
    client: TestClient, session: Session, view_id: uuid.UUID
) -> None:
    response = client.post(
        f"/grids/{GRID_KEY}/export",
        json={"viewId": str(view_id), "exportFormat": "excel", "rawValues": True},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    job = session.get(BackgroundJob, uuid.UUID(data["jobId"]))
    assert job is not None and job.job_type == EXPORT_JOB_TYPE
    assert data["jobStatus"] == "pending"
    assert data["statusPath"] == f"/jobs/{job.job_id}"
    payload = job.job_payload
    assert payload["entityType"] == ENTITY
    assert payload["columns"] == ["cityName", "postalCodeValue"]
    # FND-021: EVERY key, in priority order, direction included.
    assert payload["sortKeys"] == [
        {"field": "cityName", "direction": "asc"},
        {"field": "postalCodeValue", "direction": "desc"},
    ]
    assert payload["filterState"] == {"viewFilters": {"stateCode": "OR"}, "search": None}
    assert payload["scope"] == {"scopeKind": "filteredSet"}
    assert payload["exportFormat"] == "excel"
    assert payload["rawValues"] is True
    assert job.created_by == USER_ID


def test_export_scope_is_selection_when_one_exists(
    client: TestClient, session: Session, view_id: uuid.UUID
) -> None:
    data = client.post(
        f"/grids/{GRID_KEY}/export",
        json={
            "viewId": str(view_id),
            "selection": {"selectionKind": "explicit", "recordIds": ["68a1b2c3d4e5f6a7b"]},
        },
    ).json()["data"]
    job = session.get(BackgroundJob, uuid.UUID(data["jobId"]))
    assert job.job_payload["scope"] == {
        "scopeKind": "selection",
        "recordIds": ["68a1b2c3d4e5f6a7b"],
    }


def test_export_rejects_an_unknown_format_per_field(
    client: TestClient, session: Session, view_id: uuid.UUID
) -> None:
    response = client.post(
        f"/grids/{GRID_KEY}/export",
        json={"viewId": str(view_id), "exportFormat": "pdf"},
    )
    assert response.status_code == 422
    (error,) = response.json()["errors"]
    assert error["fieldName"] == "exportFormat"
    assert session.scalars(select(BackgroundJob)).first() is None


def test_print_enqueues_without_a_format_choice(
    client: TestClient, session: Session, view_id: uuid.UUID
) -> None:
    data = client.post(
        f"/grids/{GRID_KEY}/print",
        json={"viewId": str(view_id), "search": "lan"},
    ).json()["data"]
    job = session.get(BackgroundJob, uuid.UUID(data["jobId"]))
    assert job.job_type == PRINT_JOB_TYPE
    assert "exportFormat" not in job.job_payload
    assert job.job_payload["filterState"]["search"] == "lan"


def test_unknown_grid_and_mismatched_view_are_honest_404s(
    client: TestClient, view_id: uuid.UUID
) -> None:
    assert client.get("/grids/nope/rows", params={"view_id": str(view_id)}).status_code == 404
    assert (
        client.get(f"/grids/{GRID_KEY}/rows", params={"view_id": str(uuid7())}).status_code
        == 404
    )


# --- WTK-051 verification: the REQ-020/023/026/027 guarantees end to end ----------


def _aggregates(client: TestClient, view_id: uuid.UUID) -> dict[str, Any]:
    response = client.get(f"/grids/{GRID_KEY}/aggregates", params={"view_id": str(view_id)})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_aggregates_hold_the_whole_set_at_any_scroll_position(
    client: TestClient, view_id: uuid.UUID
) -> None:
    # REQ-026: scrolling pages the rows, never the totals. The aggregates
    # endpoint takes no cursor at all, so mid-scroll and end-of-scroll answers
    # must equal the pre-scroll answer over the entire filtered set.
    before = _aggregates(client, view_id)
    first = _rows(client, view_id, page_size=1)
    assert first["meta"]["cursor"]
    mid_scroll = _aggregates(client, view_id)
    _rows(client, view_id, page_size=1, cursor=first["meta"]["cursor"])
    after = _aggregates(client, view_id)
    assert before == mid_scroll == after
    assert before["totalCount"] == 3


def test_search_ignores_unsearchable_displayed_columns(
    client: TestClient, view_id: uuid.UUID
) -> None:
    # REQ-020: search runs over the displayed columns the registry marks
    # searchable. "972" lives only in postalCodeValue — displayed but not
    # searchable — so an armed search finds nothing rather than scanning it.
    body = _rows(client, view_id, search="972")
    assert body["meta"]["searchApplied"] is True
    assert body["data"] == []


def test_select_all_export_covers_the_filtered_set_minus_deselections(
    client: TestClient, session: Session, view_id: uuid.UUID
) -> None:
    # REQ-023 + REQ-027: select-all is the ENTIRE filtered set carried as
    # exclusions — the payload never enumerates every included ID.
    data = client.post(
        f"/grids/{GRID_KEY}/export",
        json={
            "viewId": str(view_id),
            "selection": {"selectionKind": "filteredSet", "excludedRecordIds": ["97401"]},
        },
    ).json()["data"]
    job = session.get(BackgroundJob, uuid.UUID(data["jobId"]))
    assert job.job_payload["scope"] == {
        "scopeKind": "filteredSet",
        "excludedRecordIds": ["97401"],
    }


def test_empty_explicit_selection_exports_the_entire_filtered_set(
    client: TestClient, session: Session, view_id: uuid.UUID
) -> None:
    # REQ-027's one scope rule: no effective selection → the filtered set.
    data = client.post(
        f"/grids/{GRID_KEY}/export",
        json={
            "viewId": str(view_id),
            "selection": {"selectionKind": "explicit", "recordIds": []},
        },
    ).json()["data"]
    job = session.get(BackgroundJob, uuid.UUID(data["jobId"]))
    assert job.job_payload["scope"] == {"scopeKind": "filteredSet"}


def test_malformed_selection_is_a_per_field_422_and_enqueues_nothing(
    client: TestClient, session: Session, view_id: uuid.UUID
) -> None:
    response = client.post(
        f"/grids/{GRID_KEY}/export",
        json={"viewId": str(view_id), "selection": {"selectionKind": "rows"}},
    )
    assert response.status_code == 422
    (error,) = response.json()["errors"]
    assert error["fieldName"] == "selectionKind"
    assert session.scalars(select(BackgroundJob)).first() is None


class _MemoryStore:
    """Artifact sink for the worker pass: bytes in a dict, memory:// URLs."""

    def __init__(self) -> None:
        self.artifacts: dict[str, tuple[bytes, str]] = {}

    def put(self, name: str, content: bytes, content_type: str) -> str:
        self.artifacts[name] = (content, content_type)
        return f"memory://{name}"

    def discard(self, url: str) -> None:
        self.artifacts.pop(url.removeprefix("memory://"), None)


def test_export_job_reports_progress_from_pending_to_downloadable(
    client: TestClient, session: Session, view_id: uuid.UUID
) -> None:
    # REQ-027 + DB-S11 long-run reporting, end to end: the endpoint answers a
    # followable jobID immediately; the worker pass then drives the SAME job to
    # completed with a download artifact, and completion surfaces through the
    # change feed and the requester's bell — no second notification path.
    # The bell entry references a real user row, so the session user must exist.
    session.add(AppUser(user_id=USER_ID, crm_user_id="crm-mentor", username="mentor"))
    regenerate_read_views(session)
    response = client.post(f"/grids/{GRID_KEY}/export", json={"viewId": str(view_id)})
    data = response.json()["data"]
    job_id = uuid.UUID(data["jobId"])
    assert data["jobStatus"] == "pending"
    assert data["statusPath"] == f"/jobs/{job_id}"

    store = _MemoryStore()
    assert process_next_job(session, {EXPORT_JOB_TYPE: export_job_handler(store)}) is True

    job = session.get(BackgroundJob, job_id)
    assert job.job_status == "completed"
    assert job.artifact_url == f"memory://export-{job_id}.csv"
    assert job.job_expires_at is not None
    content, content_type = store.artifacts[f"export-{job_id}.csv"]
    assert content_type == "text/csv"
    # View rendering: the artifact leads with the view's columns in display order.
    assert content.decode("utf-8").splitlines()[0] == "cityName,postalCodeValue"

    feed = session.scalars(
        select(ChangeFeedEntry).where(ChangeFeedEntry.record_id == job_id)
    ).one()
    assert (feed.entity_type, feed.change_kind) == ("backgroundJob", "updated")
    bell = session.scalars(select(Notification).where(Notification.job_id == job_id)).one()
    assert bell.user_id == USER_ID
    assert bell.notification_type == "jobCompleted"
