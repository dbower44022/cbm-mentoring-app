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
- ``normalization`` — the shared normalization services (match equality,
  phone/name/address parsing, postal lookup, shadow-column values) feeding
  validation and the duplicate match columns.
- ``postal_refresh`` — the postal-reference refresh job type composing all
  of the above.
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

__all__ = [
    "EXPORT_JOB_TYPE",
    "EXPORT_RETENTION",
    "POSTAL_REFRESH_JOB_TYPE",
    "PRINT_JOB_TYPE",
    "PRINT_RETENTION",
    "RETENTION_TRIM_JOB_TYPE",
    "ArtifactStore",
    "FeedPushTransport",
    "FeedSyncError",
    "FeedWatermark",
    "JobHandler",
    "JobOutcome",
    "ParsedAddress",
    "ParsedName",
    "PermanentJobError",
    "PostalReferenceRow",
    "PostalRefreshResult",
    "artifact_retention_trim_job",
    "claim_next_job",
    "complete_job",
    "enqueue_job",
    "export_job_handler",
    "fail_job",
    "normalize_for_match",
    "normalize_phone",
    "normalize_postal_code",
    "normalized_shadow_values",
    "parse_person_name",
    "parse_street_address",
    "postal_lookup",
    "postal_reference_refresh_job",
    "print_job_handler",
    "process_next_job",
    "read_changes_since",
    "refresh_postal_reference",
    "retry_backoff",
    "run_worker_pass",
    "sync_change_feed",
    "trim_expired_artifacts",
    "watermark_of",
]
