# Database / API standards — decision record (ENG-004)

Working session 2026-07-03. Method for this area (per Doug): Doug set four
anchor requirements, then asked Claude to PROVIDE SUGGESTIONS (a deliberate
flip from the UI-standards dictation method); suggestions presented one at a
time, Doug rules each in/out/modified. This file is authoring scratch — the
DB (ENG-004 instruction skills bound to AGP-010/011/012 and AGP-001/002/006)
is the source of truth once consolidated.

## Doug's anchor requirements (dictated 2026-07-03)

- **DB-R1 — GUIDs for unique IDs.** Primary identifiers are GUIDs, not
  auto-increment integers.
- **DB-R2 — Field names unique across all tables.** No generic reused column
  names (no `name` on every table); a field name identifies its entity
  (e.g. `mentorName`, not `name`).
- **DB-R3 — User-defined attributes.** The schema must allow user-defined
  attributes (admin/user-added fields without a developer schema change).
- **DB-R4 — Read performance over write performance.** The system is heavily
  read/lookup-skewed; design choices prioritize reads.

## Clarifications

- **DB-R2a (confirmed):** the unique-name rule includes keys. Primary keys
  are entity-named (`mentorID` on Mentor, `engagementID` on Engagement), and
  a foreign key carries the identical name as the primary key it references —
  `engagementID` means the same thing wherever it appears. No bare `id`
  columns anywhere.

## Suggestions round

- **DB-S1 — IN (2026-07-03).** GUID format is **UUIDv7** (time-ordered), not
  random UUIDv4. Rationale: read-priority (DB-R4) — v7 appends at the PK
  index edge (compact, cache-hot, recent records cluster) while keeping GUID
  benefits (merge-safe, client/API-side generation, no row-count leak);
  creation time recoverable from the ID. Cost accepted: ID reveals creation
  timestamp; generator must support v7 (app-layer generation acceptable).
- **DB-S2 — IN (2026-07-03).** User-defined attributes (DB-R3) = a
  `customAttributes` JSONB column on each entity table + an
  `attributeDefinition` registry table (name, type, entity, validation, enum
  options, label, visibility) as the schema-of-record. API validates writes
  against the registry and merges custom attributes into served records (UI
  agnostic to built-in vs user-defined). NOT EAV (join+pivot hurts reads);
  NOT dynamic ALTER TABLE (no runtime DDL, no env drift, no mid-day lock
  risk). Reads = one row; GIN index for custom-attribute queries; hot
  attributes get expression indexes. Cost accepted: typing enforced by API
  layer not DB; admin SQL uses `->>` — mitigate with generated per-entity
  views exposing registered attributes as columns.
- **DB-S3 — IN (2026-07-03).** Soft delete = `deletedAt` timestamp (null =
  live) + `deletedBy` (userID) on every entity table. Reads exclude deleted
  by default (central query-builder rule, not per-endpoint); explicit
  `includeDeleted` for admin/restore. All indexes AND unique constraints are
  **partial** (`WHERE deletedAt IS NULL`) so live-row reads never pay for
  corpses and re-adding a live duplicate of a deleted row works. Cascade
  behavior declared per relationship in the schema registry. **Includes
  DB-R2b:** structural/system columns (`deletedAt`, `deletedBy`, audit
  columns) are EXEMPT from entity-naming — identical meaning on every table
  is exactly what DB-R2 exists to guarantee.
- **DB-S4 — IN (2026-07-03).** Optimistic concurrency = `rowVersion` integer
  (system column) on every entity table, incremented on every update. Reads
  that can lead to edits carry it out; writes carry it back
  (`WHERE …ID = ? AND rowVersion = ?`); zero rows updated → API returns
  **409 with the current record in the body** (UI shows merge/refresh, can
  auto-retry a field-level PATCH when its field is untouched). No locks —
  readers never wait (DB-R4). Costs accepted: version threading is a central
  API-layer rule; bulk/system writers use read-modify-write with retry
  (never blind overwrite); customAttributes version at record level.
- **DB-S5 — IN (2026-07-03).** Audit = (1) system columns `createdAt`/
  `createdBy`/`modifiedAt`/`modifiedBy` on every entity table, API-maintained
  on every write; (2) ONE system-wide `fieldChange` history table
  (`fieldChangeID` UUIDv7, `entityType`, `recordID`, `fieldName`,
  `oldValue`/`newValue` text/JSONB, `changedAt`, `changedBy`), written when a
  **history-tracked** field changes — tracking is a per-field flag in the
  schema registry / attributeDefinition, NOT all fields. Per-field PATCH
  makes capture one insert on the write path (deprioritized side); History
  panel = one indexed lookup on `entityType + recordID`. Display-grade audit
  trail — not backups, not event sourcing; retention trim allowed later.
- **DB-S6 — IN (2026-07-03).** Full **schema registry**: every field of
  every entity (built-in AND user-defined) has a registry row — name
  (DB-R2-unique, mechanically enforced), type, label, required/validation,
  option source, history-tracked flag, visibility/grouping hints. Built-in
  rows seeded from source-controlled definitions in the same migration that
  adds the column (build fails on drift — startup registry-vs-schema check);
  custom rows created by admins at runtime (extends DB-S2's
  attributeDefinition). Served as one metadata endpoint
  (`GET /schema/{entity}`); drives server-driven UI rendering, validation,
  duplicate-detection config, history flags, exports, and admin-SQL view
  columns. One contract — the anti-enum-drift design.
- **DB-S7 — IN (2026-07-03, emphatic).** Option lists are DATA, not schema:
  shared `optionSet` + `optionValue` tables (UUIDv7 IDs, label, sort order,
  `activeFlag`); NO DB enums or CHECK constraints for choice fields. A
  field's registry row points at its option set; records store
  `optionValueID`. Retire = activeFlag off (hidden from new entry, historic
  records still render); rename = one row update; sets shareable across
  fields; custom "choice" attributes use the same tables. Kills the
  production enum-drift bug class by construction — dropdown and validator
  read the same rows. Costs accepted: label render join/cached lookup
  (hidden by read views); admin UI must show which fields use a set before
  edit.
- **DB-S8 — IN (2026-07-03).** List-read contract: (1) **keyset/seek
  pagination** with sort-value + record-ID tiebreak cursor
  (`WHERE (sortCol, entityID) > (?, ?) ORDER BY … LIMIT N`) — never OFFSET;
  stable under concurrent inserts (fits infinite-scroll cache), every page
  an equal-cost indexed seek. (2) **Counts/aggregates as a separate parallel
  query** — rows render first, count fills in. (3) **Server-side search via
  trigram (pg_trgm) indexes**; searchable-column set declared per entity in
  the schema registry. Costs accepted: no jump-to-page-N (grid standard is
  infinite scroll anyway); cursor machinery built once in the shared list
  engine; trigram write/disk cost on opt-in columns only.
- **DB-S9 — IN (2026-07-03).** Read surface = **generated per-entity views**
  (regenerated on schema-registry change): option labels joined in, custom
  attributes promoted to named columns, soft-deleted excluded. App reads AND
  admin-authored SQL data sources target views, never base tables. Admin SQL
  runs under a dedicated **read-only DB role** (SELECT on views only, no
  write verbs, statement timeout); per-source grants in app tables per the
  UI standard; **userID filtering injected server-side** (`:currentUserID`
  bound from session — declared per source, not bypassable by the author).
  DB-R2 makes view columns unambiguous, so admin SQL reads like the domain.
  Cost accepted: view regeneration is part of the custom-attribute
  lifecycle; materialization only ever a targeted later decision.
- **DB-S10 — IN (2026-07-03).** Sync = **change feed**:
  `GET /changes?since=<watermark>` → `(entityType, recordID, rowVersion,
  changeKind)` tuples + new watermark; backed by an index on `modifiedAt`
  (every table); soft deletes/restores appear as changeKinds; idempotent
  catch-up from any older watermark. Clients cache keyed
  `recordID → rowVersion`; newer version in feed invalidates + refetches
  visible records only. **Push = transport upgrade only** (same tuples over
  SSE/WebSocket later; payload/watermark/invalidation unchanged). Costs
  accepted: polling latency until push lands; record-granularity refetch;
  modifiedAt index on every table.
- **DB-S11 — IN (2026-07-03).** Background work = ONE **`job` table +
  worker**: `jobID` UUIDv7, `jobType`, `jobPayload` JSONB, `jobStatus`
  (pending/processing/completed/failed/needsAttention), `attemptCount`,
  `runAfter`, `lockedUntil` lease; claim via `FOR UPDATE SKIP LOCKED`
  (multi-worker safe, crash-reclaim by lease expiry); transient retry w/
  backoff, permanent → needsAttention. **>10s rule contract:** long
  endpoints enqueue + return `jobID`; status via `GET /jobs/{jobID}`;
  completion surfaces through the DB-S10 change feed. **Exports/prints =
  job types producing `artifact` rows** (content type, size, `expiresAt`;
  file in object storage/blob) with download link + retention expiry.
  Pattern is production-proven (cbm-client-intake V2 worker). Costs
  accepted: one worker deployment; job/artifact retention trim; per-endpoint
  10s judgment declared in the endpoint contract.
- **DB-S12 — IN (2026-07-03).** Write contract: (1) **PATCH = primary
  write** — changed fields only + `rowVersion`; no full-record PUT; POST
  create is the only whole-record write (kills the resend-drifted-values
  bug class seen in /mentoradmin). (2) **One envelope** everywhere:
  `{data, meta, errors}` (V2 API shape). (3) **Structured per-field
  validation errors** (`fieldName` + machine code + human message,
  registry-validated, ALL failures in one round trip). (4) **Duplicate
  detection API-enforced on create**: match rules declared per entity in
  the schema registry; match → rejected with candidate records; client
  merges or resubmits with a recorded override flag; detection runs against
  indexed normalized shadow columns (lowercased email, digits-only phone) —
  never UI-side. Costs accepted: shadow columns + indexes on write;
  "create anyway" affordance + override audit in history.
- **DB-S13 — IN (2026-07-03).** Supporting pieces: (1) **`userPreference`
  table** — `userID`, namespaced `preferenceKey`
  (e.g. `grid.mentorRoster.columns`), `preferenceValue` JSONB; covers saved
  grid layouts, pins, filters, tabs via one `GET/PUT /preferences/{key}`
  API; org defaults = null `userID` rows overridden by user rows; no
  per-feature tables. (2) **Parsing/normalization = server-side shared
  services**: postal-code lookup (refreshable `postalCode` reference table),
  address parsing, person-name parsing, phone → E.164; invoked in API
  validation AND feeding the SAME normalizers as DB-S12 duplicate shadow
  columns — one definition of equality. Cost accepted: postal reference
  load + refresh job (a DB-S11 job type).

## Round complete 2026-07-03 — 13/13 IN. CONSOLIDATED TO THE DB.

Recorded 2026-07-03 as ENG-004 instruction skills (DB is truth; this file +
`.claude/skills/database-api-standards/` are authoring artifacts):

- **SKL-118** — data model standard (R1–R4, R2a/b, S1–S7)
- **SKL-119** — API contract standard (S8, S10, S12, S13 preferences API)
- **SKL-120** — read surface & platform services standard (S9, S11, S13
  parsing services)

Bound via `agent_profile_has_skill` (REF-0022..0039) to BOTH triads —
storage AGP-001/002/006 and api AGP-010/011/012. Governance record:
**DEC-002 (ENG-004, Active, 2026-07-03)**. **VERIFIED:** all six profiles'
`GET /agent-profiles/{id}/contract?engagement=ENG-004` compose all three
standards; ENG-001 resolution omits them (scope isolation); UI triad
(AGP-017) unaffected.
