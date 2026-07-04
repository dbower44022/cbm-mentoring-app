"""Tests for the automation layer — worker, change-feed sync, normalization,
postal refresh (REQ-057, REQ-058, REQ-061, WTK-132)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.automation import (
    POSTAL_REFRESH_JOB_TYPE,
    FeedSyncError,
    JobOutcome,
    PermanentJobError,
    PostalReferenceRow,
    claim_next_job,
    enqueue_job,
    fail_job,
    normalize_for_match,
    normalize_phone,
    normalize_postal_code,
    normalized_shadow_values,
    parse_person_name,
    parse_street_address,
    postal_lookup,
    postal_reference_refresh_job,
    process_next_job,
    read_changes_since,
    refresh_postal_reference,
    retry_backoff,
    run_worker_pass,
    sync_change_feed,
)
from mentorapp.automation.worker import BACKOFF_BASE, BACKOFF_CAP
from mentorapp.storage import (
    BackgroundJob,
    ChangeFeedEntry,
    PostalCode,
    SchemaRegistry,
    utcnow,
    uuid7,
)

# --- background job worker (REQ-058) -----------------------------------------


def test_claim_takes_a_lease_on_the_oldest_due_job(session: Session) -> None:
    now = utcnow()
    newer = enqueue_job(session, "gridExport", run_after=now - timedelta(minutes=1))
    older = enqueue_job(session, "gridExport", run_after=now - timedelta(minutes=2))
    future = enqueue_job(session, "gridExport", run_after=now + timedelta(hours=1))
    session.commit()

    claimed = claim_next_job(session, lease=timedelta(minutes=5), now=now)
    assert claimed is not None
    assert claimed.job_id == older.job_id
    assert claimed.job_status == "processing"
    assert claimed.locked_until == now + timedelta(minutes=5)

    second = claim_next_job(session, now=now)
    assert second is not None and second.job_id == newer.job_id
    assert claim_next_job(session, now=now) is None  # future job is not due
    assert future.job_status == "pending"


def test_claim_reclaims_an_expired_lease(session: Session) -> None:
    now = utcnow()
    crashed = enqueue_job(session, "gridExport")
    crashed.job_status = "processing"
    crashed.locked_until = now - timedelta(minutes=1)
    healthy = enqueue_job(session, "gridExport")
    healthy.job_status = "processing"
    healthy.locked_until = now + timedelta(minutes=5)
    session.commit()

    reclaimed = claim_next_job(session, now=now)
    assert reclaimed is not None and reclaimed.job_id == crashed.job_id
    assert claim_next_job(session, now=now) is None  # live lease stays claimed


def test_backoff_is_exponential_and_capped() -> None:
    assert retry_backoff(1) == BACKOFF_BASE
    assert retry_backoff(2) == BACKOFF_BASE * 2
    assert retry_backoff(3) == BACKOFF_BASE * 4
    assert retry_backoff(50) == BACKOFF_CAP


def test_transient_failure_reschedules_with_backoff(session: Session) -> None:
    now = utcnow()
    job = enqueue_job(session, "gridExport")
    claim_next_job(session, now=now)
    session.commit()

    fail_job(session, job, "connection reset", now=now)
    assert job.job_status == "pending"
    assert job.attempt_count == 1
    assert job.locked_until is None
    assert job.run_after == now + retry_backoff(1)


def test_exhausted_attempts_park_as_needs_attention(session: Session) -> None:
    job = enqueue_job(session, "gridExport")
    session.commit()
    for _ in range(3):
        now = job.run_after
        assert claim_next_job(session, now=now) is not None
        fail_job(session, job, "still broken", max_attempts=3, now=now)
    assert job.job_status == "needsAttention"
    assert job.attempt_count == 3


def test_completion_stamps_artifact_and_surfaces_on_the_feed(session: Session) -> None:
    now = utcnow()
    job = enqueue_job(session, "gridExport", {"gridKey": "mentorRoster"})
    session.commit()

    def export_handler(inner: Session, claimed: BackgroundJob) -> JobOutcome:
        return JobOutcome(
            artifact_url="https://files.example/exports/roster.csv",
            artifact_retention=timedelta(days=7),
        )

    assert process_next_job(session, {"gridExport": export_handler}, now=now) is True
    assert job.job_status == "completed"
    assert job.locked_until is None
    assert job.artifact_url == "https://files.example/exports/roster.csv"
    assert job.job_expires_at == now + timedelta(days=7)
    feed = session.scalars(
        select(ChangeFeedEntry).where(ChangeFeedEntry.entity_type == "backgroundJob")
    ).all()
    assert [entry.record_id for entry in feed] == [job.job_id]
    assert feed[0].record_row_version == job.row_version


def test_permanent_error_parks_immediately(session: Session) -> None:
    job = enqueue_job(session, "gridExport")
    session.commit()

    def broken_handler(inner: Session, claimed: BackgroundJob) -> JobOutcome | None:
        raise PermanentJobError("payload references a deleted view")

    assert process_next_job(session, {"gridExport": broken_handler}) is True
    assert job.job_status == "needsAttention"
    assert job.attempt_count == 1


def test_unregistered_job_type_parks_instead_of_dropping(session: Session) -> None:
    job = enqueue_job(session, "noSuchType")
    session.commit()
    assert process_next_job(session, {}) is True
    assert job.job_status == "needsAttention"


def test_worker_pass_drains_only_what_is_due(session: Session) -> None:
    now = utcnow()
    enqueue_job(session, "gridExport")
    enqueue_job(session, "gridExport")
    enqueue_job(session, "gridExport", run_after=now + timedelta(hours=1))
    session.commit()

    handled: list[str] = []

    def handler(inner: Session, claimed: BackgroundJob) -> None:
        handled.append(str(claimed.job_id))

    assert run_worker_pass(session, {"gridExport": handler}, now=now) == 2
    assert len(handled) == 2


# --- change-feed sync (REQ-057) -----------------------------------------------


def _seed_feed(session: Session, count: int) -> list[ChangeFeedEntry]:
    entries = [
        ChangeFeedEntry(
            entity_type="mentor",
            record_id=uuid7(),
            record_row_version=1,
            change_kind="updated",
        )
        for _ in range(count)
    ]
    session.add_all(entries)
    session.commit()
    return entries


def test_catch_up_replays_idempotently_from_any_older_watermark(session: Session) -> None:
    entries = _seed_feed(session, 5)

    first_batch, watermark = read_changes_since(session, None, limit=3)
    assert [e.change_feed_entry_id for e in first_batch] == [
        e.change_feed_entry_id for e in entries[:3]
    ]
    assert watermark is not None

    rest, _ = read_changes_since(session, watermark, limit=10)
    assert [e.change_feed_entry_id for e in rest] == [
        e.change_feed_entry_id for e in entries[3:]
    ]

    # At-least-once: the same older watermark replays the same entries.
    replay, _ = read_changes_since(session, watermark, limit=10)
    assert [e.change_feed_entry_id for e in replay] == [
        e.change_feed_entry_id for e in rest
    ]

    caught_up, none_mark = read_changes_since(
        session, read_changes_since(session, None, limit=10)[1]
    )
    assert caught_up == [] and none_mark is None


class _Transport:
    def __init__(self, fail_on_batch: int | None = None) -> None:
        self.batches: list[list[ChangeFeedEntry]] = []
        self.fail_on_batch = fail_on_batch

    def push(self, entries: list[ChangeFeedEntry]) -> None:
        if self.fail_on_batch is not None and len(self.batches) + 1 == self.fail_on_batch:
            raise ConnectionError("receiver unavailable")
        self.batches.append(list(entries))


def test_sync_pushes_batches_and_returns_the_new_watermark(session: Session) -> None:
    entries = _seed_feed(session, 5)
    transport = _Transport()
    watermark = sync_change_feed(session, transport, None, batch_size=2)
    assert [len(batch) for batch in transport.batches] == [2, 2, 1]
    assert watermark is not None
    assert watermark.entry_id == entries[-1].change_feed_entry_id
    # Nothing new: watermark comes back unchanged, nothing pushed.
    assert sync_change_feed(session, transport, watermark, batch_size=2) == watermark
    assert len(transport.batches) == 3


def test_failed_push_keeps_the_durable_watermark_and_re_pushes(session: Session) -> None:
    entries = _seed_feed(session, 4)
    flaky = _Transport(fail_on_batch=2)
    with pytest.raises(FeedSyncError) as excinfo:
        sync_change_feed(session, flaky, None, batch_size=2)
    durable = excinfo.value.watermark
    assert durable is not None
    assert durable.entry_id == entries[1].change_feed_entry_id  # first batch accepted

    retry = _Transport()
    watermark = sync_change_feed(session, retry, durable, batch_size=2)
    assert [e.change_feed_entry_id for e in retry.batches[0]] == [
        e.change_feed_entry_id for e in entries[2:]
    ]
    assert watermark is not None
    assert watermark.entry_id == entries[-1].change_feed_entry_id


# --- shared normalization services (REQ-061) ----------------------------------


def test_normalizers_share_one_definition_of_equality() -> None:
    assert normalize_phone("(217) 555-0134") == "2175550134"
    assert normalize_for_match("phone", "(217) 555-0134") == normalize_phone("217.555.0134")
    assert normalize_for_match("email", "  Doug@CBM.org ") == "doug@cbm.org"


def test_person_name_parsing_handles_both_orders() -> None:
    assert parse_person_name("Bower, Doug").last_name == "Bower"
    assert parse_person_name("Bower, Doug").first_name == "Doug"
    assert parse_person_name("Doug A. Bower") == parse_person_name(" Doug  A.   Bower ")
    assert parse_person_name("Doug A. Bower").first_name == "Doug A."
    assert parse_person_name("Cher").last_name == "Cher"
    assert parse_person_name("Cher").first_name == ""


def test_street_address_parsing_is_best_effort_never_rejecting() -> None:
    parsed = parse_street_address("Apt 4, 123 Main St, Springfield, IL 62704-1234")
    assert parsed.street_line == "Apt 4, 123 Main St"
    assert parsed.city_name == "Springfield"
    assert parsed.state_code == "IL"
    assert parsed.postal_code == "62704"

    fallback = parse_street_address("PO Box 12")
    assert fallback.street_line == "PO Box 12"
    assert fallback.city_name == "" and fallback.postal_code == ""


def test_postal_lookup_normalizes_and_ignores_retired_rows(session: Session) -> None:
    session.add(PostalCode(postal_code_value="62704", city_name="Springfield", state_code="IL"))
    session.add(
        PostalCode(
            postal_code_value="99999", city_name="Gone", state_code="XX", deleted_at=utcnow()
        )
    )
    session.commit()

    assert normalize_postal_code(" 62704-1234 ") == "62704"
    hit = postal_lookup(session, "62704-1234")
    assert hit is not None and hit.city_name == "Springfield"
    assert postal_lookup(session, "99999") is None
    assert postal_lookup(session, "00000") is None


def test_shadow_values_follow_the_registry_match_rules() -> None:
    registry = {
        "mentorPhone": SchemaRegistry(
            entity_type="mentor",
            field_name="mentorPhone",
            field_type="phone",
            field_label="Phone",
            validation_rules={"duplicateMatchRules": ["byNamePhone"]},
        ),
        "mentorName": SchemaRegistry(
            entity_type="mentor",
            field_name="mentorName",
            field_type="text",
            field_label="Name",
            validation_rules={"duplicateMatchRules": ["byNamePhone"]},
        ),
        "mentorNotes": SchemaRegistry(
            entity_type="mentor",
            field_name="mentorNotes",
            field_type="text",
            field_label="Notes",
        ),
    }
    shadow = normalized_shadow_values(
        registry,
        {"mentorPhone": "(217) 555-0134", "mentorName": " Doug Bower ", "mentorNotes": "x"},
    )
    assert shadow == {
        "mentorPhoneNormalized": "2175550134",
        "mentorNameNormalized": "doug bower",
    }
    assert normalized_shadow_values(registry, {"mentorPhone": None}) == {}


# --- postal reference refresh job (REQ-061) -----------------------------------


def _snapshot() -> list[PostalReferenceRow]:
    return [
        PostalReferenceRow("62704", "Springfield", "IL"),
        PostalReferenceRow("60601", "Chicago", "IL"),
    ]


def test_refresh_upserts_idempotently_and_retires_missing(session: Session) -> None:
    session.add(PostalCode(postal_code_value="62704", city_name="Old Name", state_code="IL"))
    session.add(PostalCode(postal_code_value="11111", city_name="Stale", state_code="NY"))
    session.commit()

    result = refresh_postal_reference(session, _snapshot())
    assert (result.inserted, result.updated, result.retired) == (1, 1, 1)
    springfield = postal_lookup(session, "62704")
    assert springfield is not None and springfield.city_name == "Springfield"
    assert postal_lookup(session, "11111") is None

    again = refresh_postal_reference(session, _snapshot())
    assert (again.inserted, again.updated, again.retired) == (0, 0, 0)
    assert again.unchanged == 2

    partial = refresh_postal_reference(
        session, [PostalReferenceRow("60601", "Chicago", "IL")], full_snapshot=False
    )
    assert partial.retired == 0
    assert postal_lookup(session, "62704") is not None  # partial feeds retire nothing


def test_postal_refresh_runs_as_a_queue_job(session: Session) -> None:
    job = enqueue_job(
        session,
        POSTAL_REFRESH_JOB_TYPE,
        {"rows": [{"postalCode": "62704-0001", "city": "Springfield", "state": "IL"}]},
    )
    session.commit()

    handlers = {POSTAL_REFRESH_JOB_TYPE: postal_reference_refresh_job}
    assert process_next_job(session, handlers) is True
    assert job.job_status == "completed"
    assert postal_lookup(session, "62704") is not None


def test_postal_refresh_parks_on_a_malformed_payload(session: Session) -> None:
    job = enqueue_job(session, POSTAL_REFRESH_JOB_TYPE, {"rows": [{"oops": True}]})
    session.commit()
    handlers = {POSTAL_REFRESH_JOB_TYPE: postal_reference_refresh_job}
    assert process_next_job(session, handlers) is True
    assert job.job_status == "needsAttention"
