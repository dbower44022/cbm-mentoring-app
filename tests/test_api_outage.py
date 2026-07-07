"""``/outage`` — REQ-064's degraded-mode wire surface (WTK-159)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from identity_stub import header_user_id
from mentorapp.api.crm_writes import WriteDeferred, WriteRefused
from mentorapp.api.deps import get_current_user_id
from mentorapp.api.routers.outage import (
    crm_read_payload,
    draft_excerpt,
    draft_preserved_payload,
    get_draft_store,
    write_failure_payload,
)
from mentorapp.automation.crm_outage import (
    DraftKey,
    DraftPreserved,
    FreshRead,
    InMemoryDraftStore,
    PreservedDraft,
    StaleRead,
    UnavailableRead,
)
from mentorapp.main import create_app
from mentorapp.storage import uuid7

CAPTURED_AT = datetime(2026, 7, 5, 14, 30, tzinfo=UTC)
DOWN_SINCE = datetime(2026, 7, 6, 3, 0, tzinfo=UTC)
PRESERVED_AT = datetime(2026, 7, 6, 3, 5, tzinfo=UTC)


# --- CRM-backed reads: the degraded-read shapes, serialized ------------------------


def test_fresh_read_rides_silently() -> None:
    payload = crm_read_payload("Engagement preview", FreshRead(data={"name": "Acme"}))
    assert payload == {
        "state": "fresh",
        "notice": None,
        "capturedAt": None,
        "unavailableSince": None,
    }


def test_snapshot_read_carries_its_captured_at_label() -> None:
    result = StaleRead(
        data={"name": "Acme"},
        captured_at=CAPTURED_AT,
        unavailable_since=DOWN_SINCE,
        reason="connection timed out",
    )
    payload = crm_read_payload("Engagement preview", result)
    assert payload["state"] == "snapshot"
    assert payload["capturedAt"] == CAPTURED_AT.isoformat()
    assert payload["unavailableSince"] == DOWN_SINCE.isoformat()
    notice = payload["notice"]
    # The timestamp is the honesty: the label rides inside the message the
    # shell renders, so a snapshot can never present as fresh.
    assert CAPTURED_AT.isoformat() in notice["message"]["whatHappened"]
    assert notice["affordances"] == ["retry"]


def test_unavailable_read_names_since_when_with_detail_on_request() -> None:
    result = UnavailableRead(unavailable_since=DOWN_SINCE, reason="connection timed out")
    payload = crm_read_payload("Engagement preview", result)
    assert payload["state"] == "crmUnavailable"
    assert payload["capturedAt"] is None
    assert payload["unavailableSince"] == DOWN_SINCE.isoformat()
    notice = payload["notice"]
    assert DOWN_SINCE.isoformat() in notice["message"]["why"]
    assert notice["affordances"] == ["retry", "showDetail"]
    # Detail available on request, never dumped into the message.
    assert notice["detail"] == "connection timed out"
    assert "connection timed out" not in notice["message"]["why"]


# --- Failed CRM writes: the disposition fork, serialized ---------------------------


def test_deferred_write_presents_transient_with_its_retry_job() -> None:
    job_id = uuid7()
    payload = write_failure_payload(
        WriteDeferred(retry_job_id=job_id, reason="CRM did not answer"),
        record_title="Acme engagement",
    )
    assert payload["disposition"] == "transient"
    assert payload["retryJobId"] == str(job_id)
    assert payload["crmCause"] is None
    # Follow the retry, never re-send — a second submit would race the queue.
    assert payload["affordances"] == ["viewRetryStatus"]
    assert payload["keepsEditorState"] is True
    assert "saved" in payload["message"]["whatHappened"]


def test_refused_write_presents_terminal_with_the_crm_cause() -> None:
    payload = write_failure_payload(
        WriteRefused(crm_cause="emailAddress is not valid", fault=ValueError("rejected")),
        record_title="Acme engagement",
    )
    assert payload["disposition"] == "terminal"
    assert payload["retryJobId"] is None
    assert payload["crmCause"] == "emailAddress is not valid"
    # The cause IS what the user acts on: rendered in the message, not detail.
    assert "emailAddress is not valid" in payload["message"]["why"]
    assert payload["affordances"] == ["retrySubmit", "editAndResubmit"]
    assert payload["keepsEditorState"] is True


# --- Preserved drafts: surfacing & recovery -----------------------------------------


def _draft(author: str, target_ref: str = "rec-1", **overrides: Any) -> PreservedDraft:
    values: dict[str, Any] = {
        "key": DraftKey(author_user_id=author, target_kind="sessionLog", target_ref=target_ref),
        "content": {"notes": "Discussed cash-flow homework"},
        "first_preserved_at": PRESERVED_AT,
        "preserved_at": PRESERVED_AT,
        "reason": "CRM did not answer",
    }
    values.update(overrides)
    return PreservedDraft(**values)


def test_draft_preserved_payload_speaks_the_preservation_notice() -> None:
    author = str(uuid7())
    outcome = DraftPreserved(
        draft=_draft(author), unavailable_since=DOWN_SINCE, reason="CRM did not answer"
    )
    payload = draft_preserved_payload(outcome)
    assert payload["notice"]["whatHappened"].startswith("Your work was saved as a draft")
    assert payload["surfacing"] == ["onPreservation", "notificationBell", "onSurfaceOpen"]
    assert payload["unavailableSince"] == DOWN_SINCE.isoformat()
    # The editor keeps rendering the preserved values: the full content rides.
    assert payload["draft"]["content"] == {"notes": "Discussed cash-flow homework"}


def test_draft_excerpt_prefers_the_first_readable_value_and_truncates() -> None:
    assert draft_excerpt({"count": 3, "notes": "  Short note  "}) == "Short note"
    long_text = "x" * 200
    excerpt = draft_excerpt({"notes": long_text})
    assert len(excerpt) == 120
    assert excerpt.endswith("…")
    assert draft_excerpt({"count": 3}) == ""


@pytest.fixture()
def store() -> InMemoryDraftStore:
    return InMemoryDraftStore()


@pytest.fixture()
def client(store: InMemoryDraftStore) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_draft_store] = lambda: store
    # The D9 identity seam resolves sessions in production; these are not
    # session-lifecycle tests, so the stub names the acting user directly.
    app.dependency_overrides[get_current_user_id] = header_user_id
    return TestClient(app)


@pytest.fixture()
def user_id() -> uuid.UUID:
    return uuid7()


def _headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-ID": str(user_id)}


def test_draft_list_is_author_scoped_newest_first(
    client: TestClient, store: InMemoryDraftStore, user_id: uuid.UUID
) -> None:
    author = str(user_id)
    store.upsert(_draft(author, target_ref="rec-old"))
    later = datetime(2026, 7, 6, 4, 0, tzinfo=UTC)
    store.upsert(_draft(author, target_ref="rec-new", preserved_at=later))
    store.upsert(_draft(str(uuid7()), target_ref="rec-other"))

    response = client.get("/outage/drafts", headers=_headers(user_id))
    assert response.status_code == 200
    body = response.json()
    assert body["errors"] is None
    assert body["meta"]["count"] == 2
    refs = [draft["targetRef"] for draft in body["data"]["drafts"]]
    assert refs == ["rec-new", "rec-old"]
    first = body["data"]["drafts"][0]
    # Wording rides from its one home: the offer and the honest discard
    # confirmation, so the shell never restates either.
    assert first["offer"]["whatHappened"].startswith("A draft from")
    assert "administrator can restore" in first["discardConfirmation"]["whatNext"]
    assert first["excerpt"] == "Discussed cash-flow homework"


def test_surface_open_offer_finds_the_draft_without_consuming_it(
    client: TestClient, store: InMemoryDraftStore, user_id: uuid.UUID
) -> None:
    store.upsert(_draft(str(user_id)))
    response = client.get("/outage/drafts/sessionLog/rec-1", headers=_headers(user_id))
    assert response.status_code == 200
    draft = response.json()["data"]["draft"]
    assert draft["content"] == {"notes": "Discussed cash-flow homework"}
    # Reading the offer is a restore, not a consume: the draft survives.
    again = client.get("/outage/drafts/sessionLog/rec-1", headers=_headers(user_id))
    assert again.json()["data"]["draft"] is not None


def test_surface_open_with_no_draft_is_a_clean_none(
    client: TestClient, user_id: uuid.UUID
) -> None:
    response = client.get("/outage/drafts/sessionLog/rec-1", headers=_headers(user_id))
    assert response.status_code == 200
    assert response.json()["data"]["draft"] is None


def test_discard_is_explicit_author_scoped_and_idempotent(
    client: TestClient, store: InMemoryDraftStore, user_id: uuid.UUID
) -> None:
    store.upsert(_draft(str(user_id)))
    other_author = uuid7()

    # Another author's discard cannot touch this draft: keys carry the author.
    response = client.post(
        "/outage/drafts/sessionLog/rec-1/discard", headers=_headers(other_author)
    )
    assert response.json()["data"]["discarded"] is False
    assert store.get(DraftKey(str(user_id), "sessionLog", "rec-1")) is not None

    response = client.post("/outage/drafts/sessionLog/rec-1/discard", headers=_headers(user_id))
    assert response.json()["data"]["discarded"] is True
    assert store.get(DraftKey(str(user_id), "sessionLog", "rec-1")) is None

    # A double discard (submit raced a manual discard) is a no-op, not an error.
    again = client.post("/outage/drafts/sessionLog/rec-1/discard", headers=_headers(user_id))
    assert again.status_code == 200
    assert again.json()["data"]["discarded"] is False


def test_missing_user_header_is_the_standard_422(client: TestClient) -> None:
    assert client.get("/outage/drafts").status_code == 422


def test_unwired_draft_store_fails_loudly(user_id: uuid.UUID) -> None:
    app = create_app()
    # Identity resolves before the draft store; stub it so the seam under
    # test (the unwired store) is the one that fails.
    app.dependency_overrides[get_current_user_id] = header_user_id
    client = TestClient(app)
    with pytest.raises(RuntimeError, match="draft store provider is not wired"):
        client.get("/outage/drafts", headers=_headers(user_id))
