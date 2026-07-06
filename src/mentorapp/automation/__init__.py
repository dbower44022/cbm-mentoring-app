"""Automation layer — background worker, change-feed sync, shared normalization.

Designs REQ-057/REQ-058/REQ-061 as code (WTK-132), one canonical home per
concept, layered storage → automation → api:

- ``worker`` — the background-job worker over the one queue: safe lease with
  crash reclaim, exponential-backoff retry, ``needsAttention`` parking, and
  artifact production with retention expiry.
- ``artifact_jobs`` — the export/print job types producing downloadable
  artifacts off the generated read views, plus the retention trim that
  reclaims them when ``jobExpiresAt`` passes (WTK-141).
- ``change_feed`` — idempotent watermark catch-up reads and the push
  transport, both at-least-once against consumers that dedup on entry ID.
- ``crm_outage`` — the REQ-064 outage processes (WTK-153):
  crm_outage_degradation (reads say "unavailable since/because" or serve a
  snapshot labelled stale — never empty, never stale-as-fresh, behind a
  consecutive-failure breaker) and crm_draft_preservation (in-progress work
  upserted locally as one draft per author+target while the CRM is down).
- ``normalization`` — the shared normalization services (match equality,
  phone/name/address parsing, postal lookup, shadow-column values) feeding
  validation and the duplicate match columns.
- ``postal_refresh`` — the postal-reference refresh job type composing all
  of the above.
- ``workprocess_engine`` — the REQ-042 execution frame (WTK-092): walk a
  registration's step graph, hold answers pending, and commit or discard
  atomically through the per-workprocess handler seam.
"""

from mentorapp.automation.artifact_jobs import (
    EXPORT_JOB_TYPE,
    EXPORT_RETENTION,
    PRINT_JOB_TYPE,
    PRINT_RETENTION,
    RETENTION_TRIM_JOB_TYPE,
    ArtifactStore,
    artifact_retention_trim_job,
    export_job_handler,
    print_job_handler,
    trim_expired_artifacts,
)
from mentorapp.automation.change_feed import (
    FeedPushTransport,
    FeedSyncError,
    FeedWatermark,
    read_changes_since,
    sync_change_feed,
    watermark_of,
)
from mentorapp.automation.crm_outage import (
    CrmAvailability,
    CrmHealthMonitor,
    CrmReadResult,
    CrmSnapshot,
    DraftKey,
    DraftPreserved,
    DraftStore,
    FreshRead,
    InMemoryDraftStore,
    PreservedDraft,
    StaleRead,
    SubmitAccepted,
    SubmitOutcome,
    UnavailableRead,
    degraded_crm_read,
    discard_draft,
    preserve_draft,
    recoverable_drafts,
    submit_or_preserve,
)
from mentorapp.automation.normalization import (
    ParsedAddress,
    ParsedName,
    normalize_for_match,
    normalize_phone,
    normalize_postal_code,
    normalized_shadow_values,
    parse_person_name,
    parse_street_address,
    postal_lookup,
)
from mentorapp.automation.postal_refresh import (
    POSTAL_REFRESH_JOB_TYPE,
    PostalReferenceRow,
    PostalRefreshResult,
    postal_reference_refresh_job,
    refresh_postal_reference,
)
from mentorapp.automation.worker import (
    JobHandler,
    JobOutcome,
    PermanentJobError,
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
    process_next_job,
    retry_backoff,
    run_worker_pass,
)
from mentorapp.automation.workprocess_engine import (
    LONG_RUN_NOTIFICATION_AFTER,
    CommitHandlerRegistry,
    InMemoryCommitHandlers,
    NotCurrentStepError,
    RunNotCompletableError,
    RunNotInFlightError,
    UnknownBranchError,
    WorkprocessCommitHandler,
    WorkprocessCommitPayload,
    WorkprocessRunError,
    answer_step,
    cancel_run,
    commit_run,
    launch_run,
    step_graph_problems,
)

__all__ = [
    "EXPORT_JOB_TYPE",
    "EXPORT_RETENTION",
    "LONG_RUN_NOTIFICATION_AFTER",
    "POSTAL_REFRESH_JOB_TYPE",
    "PRINT_JOB_TYPE",
    "PRINT_RETENTION",
    "RETENTION_TRIM_JOB_TYPE",
    "ArtifactStore",
    "CommitHandlerRegistry",
    "CrmAvailability",
    "CrmHealthMonitor",
    "CrmReadResult",
    "CrmSnapshot",
    "DraftKey",
    "DraftPreserved",
    "DraftStore",
    "FeedPushTransport",
    "FeedSyncError",
    "FeedWatermark",
    "FreshRead",
    "InMemoryCommitHandlers",
    "InMemoryDraftStore",
    "JobHandler",
    "JobOutcome",
    "NotCurrentStepError",
    "ParsedAddress",
    "ParsedName",
    "PermanentJobError",
    "PostalReferenceRow",
    "PostalRefreshResult",
    "PreservedDraft",
    "RunNotCompletableError",
    "RunNotInFlightError",
    "StaleRead",
    "SubmitAccepted",
    "SubmitOutcome",
    "UnavailableRead",
    "UnknownBranchError",
    "WorkprocessCommitHandler",
    "WorkprocessCommitPayload",
    "WorkprocessRunError",
    "answer_step",
    "artifact_retention_trim_job",
    "cancel_run",
    "claim_next_job",
    "commit_run",
    "complete_job",
    "degraded_crm_read",
    "discard_draft",
    "enqueue_job",
    "export_job_handler",
    "fail_job",
    "launch_run",
    "normalize_for_match",
    "normalize_phone",
    "normalize_postal_code",
    "normalized_shadow_values",
    "parse_person_name",
    "parse_street_address",
    "postal_lookup",
    "postal_reference_refresh_job",
    "preserve_draft",
    "print_job_handler",
    "process_next_job",
    "read_changes_since",
    "recoverable_drafts",
    "refresh_postal_reference",
    "retry_backoff",
    "run_worker_pass",
    "step_graph_problems",
    "submit_or_preserve",
    "sync_change_feed",
    "trim_expired_artifacts",
    "watermark_of",
]
