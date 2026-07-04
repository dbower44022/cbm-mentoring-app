# The read surface & platform services standard

Approved by Doug 2026-07-03 (source: `docs/database-api-standards-discussion.md`,
suggestions DB-S9, DB-S11, DB-S13.2, all ruled IN). Applies to the read
surface, background work, and shared services of the CBM Mentoring Custom
App. Nothing here is optional.

## Generated read views + admin-authored SQL (DB-S9)

- For every entity the system **generates a read view** (regenerated on any
  schema-registry change): base columns, option-value labels already joined
  in, registered custom attributes promoted from JSONB to named columns,
  soft-deleted rows already excluded.
- **Views are the official read surface.** The app's own list/detail reads
  AND the admin-authored SQL data sources (the grid standard's data-source
  system) target views — never base tables. Because column names are unique
  system-wide, admin SQL over views reads like the business domain; JSONB
  operators, label joins, and `deletedAt` filters are invisible.
- **Admin SQL executes under a dedicated read-only database role**: SELECT
  on views only, no base tables, no write verbs, statement timeout set. The
  worst admin SQL can do is a slow query, and the timeout bounds that.
- **Per-source grants** (which staff roles may run a data source) live in
  app tables per the UI standard — that is the security boundary.
- **userID filtering is injected, never trusted:** a data source declares
  whether it is user-scoped; the API binds the session user's ID
  server-side (`:currentUserID`). The author references it but cannot
  bypass it.
- View regeneration is part of the custom-attribute lifecycle (admin adds
  attribute → registry row → view regenerated, automatic). Materializing a
  specific heavy view is a targeted later decision, not part of the
  standard.

## Background jobs & artifacts (DB-S11)

- **One `job` table + worker** is the standard for all background work:
  `jobID` (UUIDv7), `jobType`, `jobPayload` (JSONB), `jobStatus`
  (pending / processing / completed / failed / needsAttention),
  `attemptCount`, `runAfter`, `lockedUntil` (lease).
- Workers claim due jobs with `FOR UPDATE SKIP LOCKED` — multiple workers
  are safe by construction; a crashed worker's lease expires and the job is
  reclaimed. Transient failures retry with backoff to a cap; permanent
  failures park as `needsAttention`. (This pattern is production-proven in
  the cbm-client-intake V2 delivery worker.)
- **The >10-second rule (API contract):** any endpoint whose work may exceed
  the threshold enqueues and immediately returns `jobID`; the client follows
  `GET /jobs/{jobID}` for status/progress; completion also appears in the
  change feed, so open windows learn without dedicated polling. The 10s
  judgment is declared per endpoint in its contract, not discovered in
  production.
- **Exports and print/document generation are job types** whose completion
  writes an **`artifact`** row (`artifactID`, content type, size,
  `expiresAt`) with the file in object storage (or a blob column at CBM
  scale). The UI gets a download link; artifacts expire on a retention
  schedule. Big result sets never travel in API responses.
- Postgres-as-queue is the deliberate scale call — no external broker to
  operate.

## Parsing & normalization services (DB-S13)

- Address parsing, person-name parsing (first/last/suffix), postal-code →
  city/state lookup, and phone → E.164 normalization are **server-side
  shared services** — one implementation each, never duplicated in UI code.
- Postal-code lookup is backed by a `postalCode` reference table,
  refreshable as data (a job type), not hardcoded.
- These services are invoked by the API during validation AND feed the
  **same normalizers** used by the duplicate-detection shadow columns — so
  "what makes two addresses/phones/names equal" has exactly one definition
  in the system.
