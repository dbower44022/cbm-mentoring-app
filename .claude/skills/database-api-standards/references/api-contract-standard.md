# The API contract standard

Approved by Doug 2026-07-03 (source: `docs/database-api-standards-discussion.md`,
suggestions DB-S8, DB-S10, DB-S12, DB-S13.1, all ruled IN). Applies to every
API endpoint in the CBM Mentoring Custom App. Nothing here is optional.

## List reads (DB-S8)

Every grid/list endpoint follows three rules — this is the data-layer
contract behind the grid standard's infinite scroll and server-side truth:

1. **Keyset (seek) pagination, never OFFSET.** Pages are "the next N rows
   after cursor X"; the cursor is the sort value **plus the record ID as
   tiebreak** (`WHERE (sortCol, entityID) > (?, ?) ORDER BY sortCol,
   entityID LIMIT N`). Every page is an equal-cost indexed seek, and pages
   are stable under concurrent inserts — which is what the infinite-scroll
   cache requires. The UUIDv7 key is always available as the tiebreak.
   Cursor encode/decode is built once in the shared list engine. (No
   jump-to-page-N exists; the grid standard is infinite scroll.)
2. **Counts and aggregates are a separate query**, issued in parallel with
   the first page: rows render immediately, the count/aggregates fill in
   when they land. Both are computed server-side over the entire filtered
   result set.
3. **Search is server-side** against trigram (`pg_trgm`) indexes on the
   searchable text columns — real contains/fuzzy matching that stays
   indexed. The searchable-column set is declared per entity in the schema
   registry (opt-in, not everything-indexed).

## The write contract (DB-S12)

1. **PATCH is the primary write verb.** An edit sends only the changed
   fields plus `rowVersion`. Full-record PUT does not exist; POST (create)
   is the only whole-record write. Unchanged fields never travel — so a
   stale/drifted unchanged value can never fail an unrelated save.
2. **One response envelope everywhere:** `{data, meta, errors}`. No endpoint
   invents its own shape.
3. **Validation errors are structured per field:** each `errors[]` entry
   carries `fieldName`, a machine-readable code, and a human message,
   validated against the schema registry. A multi-field save reports ALL
   failures in one round trip, never first-failure-only. The error shape is
   part of the server-driven rendering contract.
4. **Duplicate detection is enforced by the API on create.** Match rules are
   declared per entity in the schema registry (e.g. person by email, by
   normalized name+phone). A matching create is rejected **with the
   candidate records in the response**; the client either merges into an
   existing record or explicitly resubmits with an override flag, which is
   itself recorded (history). Detection runs server-side against indexed
   normalized shadow columns (lowercased email, digits-only phone) — never
   UI-side lookup.
5. **Concurrency:** stale `rowVersion` → **409 with the current record in
   the body** (see the data model standard).

## The change feed — same-user sync, upgradeable to push (DB-S10)

- One endpoint: `GET /changes?since=<watermark>` returns
  `(entityType, recordID, rowVersion, changeKind)` tuples for everything
  modified after the watermark, plus a new watermark. Backed by the
  `modifiedAt` index on every table. Soft deletes and restores appear as
  changeKinds. The feed is idempotent — catch-up from any older watermark is
  always correct.
- Clients keep grid/record caches keyed `recordID → rowVersion`; a feed
  entry with a newer version invalidates that entry and only visible records
  are refetched. Sync traffic is proportional to what changed.
- **Server push is a transport upgrade, not a redesign:** the same tuples
  later stream over SSE/WebSocket; payload, watermark, and invalidation
  logic do not change — the polling interval just drops to zero.
- Job completion (see platform services standard) also surfaces through this
  feed — there is no second notification path.

## The preferences API (DB-S13)

- All view/pin/layout/filter persistence goes through one mechanism:
  `GET/PUT /preferences/{key}` over the `userPreference` table
  (`userPreferenceID` UUIDv7, `userID`, namespaced `preferenceKey` such as
  `grid.mentorRoster.columns` or `nav.pinnedViews`, `preferenceValue`
  JSONB, standard system columns).
- Org-wide defaults are rows with null `userID`; a user's own row overrides.
- No per-feature preference tables or columns — a new grid feature needs no
  migration.
