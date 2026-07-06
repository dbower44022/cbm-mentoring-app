"""The REQ-064 outage processes (WTK-153): degradation honesty and draft safety."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from mentorapp.automation import (
    CrmHealthMonitor,
    CrmSnapshot,
    DraftKey,
    DraftPreserved,
    FreshRead,
    InMemoryDraftStore,
    StaleRead,
    SubmitAccepted,
    UnavailableRead,
    degraded_crm_read,
    discard_draft,
    preserve_draft,
    recoverable_drafts,
    submit_or_preserve,
)
from mentorapp.crm.auth import CrmUnavailableError

T0 = datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)


def _key(ref: str = "client-1") -> DraftKey:
    return DraftKey(author_user_id="mentor-1", target_kind="crmClient", target_ref=ref)


def _down() -> Any:
    raise CrmUnavailableError("EspoCRM did not answer")


def _open_monitor(now: datetime = T0) -> CrmHealthMonitor:
    monitor = CrmHealthMonitor()
    for _ in range(3):
        degraded_crm_read(monitor, _down, now=now)
    assert not monitor.availability().available
    return monitor


# --- crm_outage_degradation -------------------------------------------------


def test_fresh_read_passes_through_and_stays_available() -> None:
    monitor = CrmHealthMonitor()
    result = degraded_crm_read(monitor, lambda: ["row"], now=T0)
    assert result == FreshRead(data=["row"])
    assert monitor.availability().available


def test_outage_reads_say_unavailable_specifically_not_empty() -> None:
    monitor = CrmHealthMonitor()
    result = degraded_crm_read(monitor, _down, now=T0)
    assert isinstance(result, UnavailableRead)
    assert result.reason == "EspoCRM did not answer"
    # Specifically NOT a shape that could render as an empty result set.
    assert not hasattr(result, "data")


def test_snapshot_is_served_labelled_stale_never_as_fresh() -> None:
    monitor = CrmHealthMonitor()
    snapshot = CrmSnapshot(data=["old row"], captured_at=T0 - timedelta(minutes=5))
    result = degraded_crm_read(monitor, _down, snapshot=snapshot, now=T0)
    assert isinstance(result, StaleRead)
    assert not isinstance(result, FreshRead)
    assert result.data == ["old row"]
    assert result.captured_at == snapshot.captured_at
    assert result.reason == "EspoCRM did not answer"


def test_breaker_opens_after_threshold_and_short_circuits() -> None:
    monitor = _open_monitor()
    state = monitor.availability()
    assert state.unavailable_since == T0
    assert state.reason == "EspoCRM did not answer"

    def must_not_be_called() -> Any:
        raise AssertionError("open breaker must not touch the CRM")

    inside_cooldown = T0 + timedelta(seconds=5)
    result = degraded_crm_read(monitor, must_not_be_called, now=inside_cooldown)
    assert isinstance(result, UnavailableRead)
    assert result.unavailable_since == T0


def test_probe_after_cooldown_closes_the_breaker_on_success() -> None:
    monitor = _open_monitor()
    after_cooldown = T0 + timedelta(seconds=31)
    result = degraded_crm_read(monitor, lambda: ["row"], now=after_cooldown)
    assert result == FreshRead(data=["row"])
    assert monitor.availability().available


def test_failed_probe_keeps_the_breaker_open_one_probe_per_cooldown() -> None:
    monitor = _open_monitor()
    probe_time = T0 + timedelta(seconds=31)
    assert isinstance(degraded_crm_read(monitor, _down, now=probe_time), UnavailableRead)
    # The original outage start survives the failed probe (banner says since-when).
    assert monitor.availability().unavailable_since == T0

    def must_not_be_called() -> Any:
        raise AssertionError("second call inside the probe cooldown must short-circuit")

    just_after_probe = probe_time + timedelta(seconds=5)
    assert isinstance(
        degraded_crm_read(monitor, must_not_be_called, now=just_after_probe),
        UnavailableRead,
    )


def test_success_resets_the_consecutive_failure_count() -> None:
    monitor = CrmHealthMonitor()
    for _ in range(2):
        degraded_crm_read(monitor, _down, now=T0)
    degraded_crm_read(monitor, lambda: "ok", now=T0)
    for _ in range(2):
        degraded_crm_read(monitor, _down, now=T0)
    # 2 + 2 failures with a success between: never three straight, still closed.
    assert monitor.availability().available


def test_non_outage_read_errors_propagate_untouched() -> None:
    monitor = CrmHealthMonitor()

    def refused() -> Any:
        raise PermissionError("CRM said no")

    with pytest.raises(PermissionError):
        degraded_crm_read(monitor, refused, now=T0)
    assert monitor.availability().available


# --- crm_draft_preservation --------------------------------------------------


def test_preserve_draft_is_idempotent_per_key() -> None:
    store = InMemoryDraftStore()
    first = preserve_draft(store, _key(), {"name": "Acme"}, reason="outage", now=T0)
    later = T0 + timedelta(minutes=2)
    second = preserve_draft(store, _key(), {"name": "Acme Ltd"}, reason="outage", now=later)
    assert len(recoverable_drafts(store, "mentor-1")) == 1
    assert second.content == {"name": "Acme Ltd"}
    assert second.first_preserved_at == first.first_preserved_at == T0
    assert second.preserved_at == later


def test_submit_or_preserve_accepts_and_clears_the_draft_on_success() -> None:
    store = InMemoryDraftStore()
    monitor = CrmHealthMonitor()
    preserve_draft(store, _key(), {"name": "Acme"}, reason="outage", now=T0)
    outcome = submit_or_preserve(
        monitor, store, _key(), {"name": "Acme"}, lambda: "crm-id-9", now=T0
    )
    assert outcome == SubmitAccepted(result="crm-id-9")
    assert recoverable_drafts(store, "mentor-1") == []


def test_submit_outage_preserves_the_work_with_the_reason() -> None:
    store = InMemoryDraftStore()
    monitor = CrmHealthMonitor()
    outcome = submit_or_preserve(monitor, store, _key(), {"name": "Acme"}, _down, now=T0)
    assert isinstance(outcome, DraftPreserved)
    assert outcome.reason == "EspoCRM did not answer"
    assert outcome.draft.content == {"name": "Acme"}
    assert recoverable_drafts(store, "mentor-1") == [outcome.draft]


def test_open_breaker_preserves_without_attempting_the_submit() -> None:
    store = InMemoryDraftStore()
    monitor = _open_monitor()

    def must_not_be_called() -> Any:
        raise AssertionError("open breaker must not attempt the submit")

    inside_cooldown = T0 + timedelta(seconds=5)
    outcome = submit_or_preserve(
        monitor, store, _key(), {"name": "Acme"}, must_not_be_called, now=inside_cooldown
    )
    assert isinstance(outcome, DraftPreserved)
    assert outcome.unavailable_since == T0


def test_non_outage_submit_failures_propagate_and_preserve_nothing() -> None:
    # The WTK-152 boundary: validation/terminal write faults are the write
    # contract's to classify — draft preservation must not swallow them.
    store = InMemoryDraftStore()
    monitor = CrmHealthMonitor()

    def rejected() -> Any:
        raise ValueError("duplicate")

    with pytest.raises(ValueError, match="duplicate"):
        submit_or_preserve(monitor, store, _key(), {"name": "Acme"}, rejected, now=T0)
    assert recoverable_drafts(store, "mentor-1") == []


def test_recovery_lists_most_recent_first_and_discard_is_idempotent() -> None:
    store = InMemoryDraftStore()
    preserve_draft(store, _key("a"), {"n": 1}, reason="outage", now=T0)
    preserve_draft(store, _key("b"), {"n": 2}, reason="outage", now=T0 + timedelta(minutes=1))
    drafts = recoverable_drafts(store, "mentor-1")
    assert [d.key.target_ref for d in drafts] == ["b", "a"]
    discard_draft(store, _key("b"))
    discard_draft(store, _key("b"))  # double discard is a no-op, not an error
    assert [d.key.target_ref for d in recoverable_drafts(store, "mentor-1")] == ["a"]
