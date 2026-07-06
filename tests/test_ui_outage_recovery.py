"""Outage messaging & draft recovery design gate: REQ-064's UI states (WTK-154)."""

from __future__ import annotations

import pytest

from mentorapp.ui.outage_recovery import (
    CRM_READ_FRESH,
    CRM_READ_KINDS,
    CRM_READ_SNAPSHOT,
    CRM_READ_UNAVAILABLE,
    DRAFT_PRESERVED,
    DRAFT_SURFACING,
    FAILURE_KEEPS_EDITOR_STATE,
    DraftRecovery,
    PreservedDraftRef,
    UnknownDraftError,
    discard_draft_confirmation,
    resolve_crm_read_state,
    write_failure_notice,
)

NOTES_DRAFT = PreservedDraftRef(
    author_user_id="u-1",
    surface_key="sessionPrep",
    entity_type="session",
    record_id="s-1",
    saved_at="2026-07-05 14:02",
    excerpt="Discussed cash-flow homework…",
)


# --- CRM-backed reads: fresh / snapshot / unavailable ------------------------------


def test_fresh_reads_render_silently() -> None:
    assert resolve_crm_read_state("Client details", CRM_READ_FRESH) is None


def test_snapshot_is_labelled_with_its_capture_time() -> None:
    notice = resolve_crm_read_state(
        "Client details", CRM_READ_SNAPSHOT, captured_at="2026-07-05 13:40"
    )
    assert notice is not None and notice.kind == CRM_READ_SNAPSHOT
    # Stale never masquerades as fresh: the capture time is IN the message.
    assert "2026-07-05 13:40" in notice.message.what_happened
    assert notice.affordances == ("retry",)


def test_snapshot_without_a_capture_label_is_refused() -> None:
    with pytest.raises(ValueError, match="captured_at"):
        resolve_crm_read_state("Client details", CRM_READ_SNAPSHOT)


def test_unavailable_read_names_the_crm_with_retry_and_detail() -> None:
    notice = resolve_crm_read_state(
        "Client details",
        CRM_READ_UNAVAILABLE,
        unavailable_since="14:01",
        reason="connect timeout to crm.example.org",
    )
    assert notice is not None and notice.kind == CRM_READ_UNAVAILABLE
    assert "CRM" in notice.message.why and "since 14:01" in notice.message.why
    assert notice.affordances == ("retry", "showDetail")
    # Detail is available on request, never dumped into the educate triple.
    assert notice.detail == "connect timeout to crm.example.org"
    assert "connect timeout" not in notice.message.why


def test_unknown_read_kind_is_a_caller_bug() -> None:
    assert CRM_READ_KINDS == ("fresh", "snapshot", "crmUnavailable")
    with pytest.raises(ValueError, match="unknown CRM read kind"):
        resolve_crm_read_state("Client details", "cached")


# --- Failed CRM writes: the two dispositions --------------------------------------


def test_transient_failure_says_the_work_is_safe_and_tracked() -> None:
    notice = write_failure_notice(
        "transient", record_title="Ada Lovelace", crm_cause="", retry_job_id="job-9"
    )
    assert notice.disposition == "transient"
    assert notice.retry_job_id == "job-9"
    # No resubmission affordance: a second submit would race the queued job.
    assert notice.affordances == ("viewRetryStatus",)
    assert "saved" in notice.message.what_happened


def test_transient_failure_requires_its_retry_job() -> None:
    with pytest.raises(ValueError, match="job id"):
        write_failure_notice("transient", record_title="Ada Lovelace", crm_cause="")


def test_terminal_failure_carries_the_crm_cause_and_retry_affordance() -> None:
    notice = write_failure_notice(
        "terminal",
        record_title="Ada Lovelace",
        crm_cause="'email' is not a valid address",
    )
    assert notice.disposition == "terminal"
    # REQ-064: the specific cause surfaces immediately, in the message itself.
    assert "'email' is not a valid address" in notice.message.why
    assert notice.affordances == ("retrySubmit", "editAndResubmit")
    assert notice.retry_job_id is None


def test_terminal_failure_never_carries_a_job_id() -> None:
    with pytest.raises(ValueError, match="never enqueued"):
        write_failure_notice(
            "terminal", record_title="Ada", crm_cause="refused", retry_job_id="job-9"
        )


def test_a_failed_save_never_clears_the_editor() -> None:
    assert FAILURE_KEEPS_EDITOR_STATE is True


# --- Preserved drafts: surfacing & recovery ----------------------------------------


def test_drafts_surface_at_preservation_the_bell_and_reopen() -> None:
    assert DRAFT_SURFACING == ("onPreservation", "notificationBell", "onSurfaceOpen")
    assert "nothing you wrote was lost" in DRAFT_PRESERVED.why


def test_reopening_the_authoring_surface_offers_the_draft() -> None:
    recovery = DraftRecovery()
    assert recovery.draft_preserved(NOTES_DRAFT) is DRAFT_PRESERVED
    offered = recovery.offer_for("u-1", "sessionPrep", "session", "s-1")
    assert offered is not None
    draft, message = offered
    assert draft is NOTES_DRAFT
    # The offer names when the work was preserved so the user can judge it.
    assert NOTES_DRAFT.saved_at in message.what_happened


def test_no_draft_means_no_offer_and_other_authors_never_see_it() -> None:
    recovery = DraftRecovery((NOTES_DRAFT,))
    assert recovery.offer_for("u-1", "sessionPrep", "session", "s-2") is None
    assert recovery.offer_for("u-2", "sessionPrep", "session", "s-1") is None


def test_same_target_preservation_upserts_never_duplicates() -> None:
    recovery = DraftRecovery((NOTES_DRAFT,))
    newer = PreservedDraftRef(
        author_user_id="u-1",
        surface_key="sessionPrep",
        entity_type="session",
        record_id="s-1",
        saved_at="2026-07-05 15:30",
        excerpt="Revised homework notes…",
    )
    recovery.draft_preserved(newer)
    assert recovery.recoverable() == (newer,)


def test_restore_does_not_consume_the_draft() -> None:
    recovery = DraftRecovery((NOTES_DRAFT,))
    assert recovery.restore(NOTES_DRAFT) is NOTES_DRAFT
    # A crash after restore still finds the draft waiting.
    assert recovery.recoverable() == (NOTES_DRAFT,)


def test_only_a_successful_submit_or_explicit_discard_clears() -> None:
    recovery = DraftRecovery((NOTES_DRAFT,))
    recovery.submit_succeeded(NOTES_DRAFT)
    assert recovery.recoverable() == ()
    # Clearing again is fine — the submit may fan out from several windows.
    recovery.submit_succeeded(NOTES_DRAFT)

    recovery.draft_preserved(NOTES_DRAFT)
    recovery.discard(NOTES_DRAFT)
    assert recovery.recoverable() == ()
    with pytest.raises(UnknownDraftError):
        recovery.discard(NOTES_DRAFT)
    with pytest.raises(UnknownDraftError):
        recovery.restore(NOTES_DRAFT)


def test_discard_confirmation_is_honest_about_restorability() -> None:
    confirmation = discard_draft_confirmation(NOTES_DRAFT)
    assert NOTES_DRAFT.saved_at in confirmation.what_happened
    # Soft-delete honesty: never "cannot be undone".
    assert "administrator can restore" in confirmation.what_next
    assert "cannot be undone" not in confirmation.what_next.lower()
