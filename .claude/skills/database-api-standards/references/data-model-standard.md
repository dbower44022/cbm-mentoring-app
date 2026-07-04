# The data model standard

Approved by Doug 2026-07-03 (source: `docs/database-api-standards-discussion.md`,
anchor requirements DB-R1..R4 + suggestions DB-S1..S7, all ruled IN). Applies
to every entity table in the CBM Mentoring Custom App. Nothing here is
optional.

## Identifiers (DB-R1, DB-S1)

- Every unique ID is a GUID — **never** an auto-increment integer.
- GUID format is **UUIDv7** (time-ordered), not random UUIDv4: v7 keys append
  at the primary-key index edge (compact, cache-hot, recent records cluster
  physically — the read-priority choice), while keeping GUID benefits
  (merge/import safety, client/API-side generation before insert, no
  row-count leak). A record's creation time is recoverable from its ID.
  Generation may happen in the app layer.

## Naming (DB-R2, DB-R2a, DB-R2b)

- **Every column name means exactly one thing system-wide.** Field names are
  unique across all tables: no `name`, no bare `status` — `mentorName`,
  `engagementStatus`.
- **Keys are entity-named**: the primary key is `mentorID` on Mentor,
  `engagementID` on Engagement; a foreign key carries the **identical name**
  as the primary key it references. `engagementID` means the same thing
  wherever it appears; joins are self-documenting
  (`ON Engagement.mentorID = Mentor.mentorID`). No bare `id` columns.
- **Exemption — structural/system columns** are identical on every table and
  exempt from entity-naming (identical meaning everywhere is exactly what the
  uniqueness rule exists to guarantee): `deletedAt`, `deletedBy`, `createdAt`,
  `createdBy`, `modifiedAt`, `modifiedBy`, `rowVersion`, `customAttributes`.
- Name uniqueness is **mechanically enforced by the schema registry** (see
  below), not by convention.

## User-defined attributes (DB-R3, DB-S2)

- Mechanism: a **`customAttributes` JSONB column** on each entity table +
  an **`attributeDefinition` registry** (name, type, entity, validation,
  enum/option source, label, visibility) as the schema-of-record for what's
  allowed in it.
- The API validates writes against the registry and merges custom attributes
  into served records — the UI never cares whether a field is built-in or
  user-defined.
- **Not EAV** (join + pivot per attribute is the worst shape for a
  read-skewed system); **not dynamic ALTER TABLE** (no runtime DDL rights, no
  environment drift, no mid-day lock risk — the failure mode of an admin
  action is a bad row, never a broken table).
- Reads stay one-row-one-fetch. GIN index for custom-attribute queries; a hot
  custom attribute may get a dedicated expression index without schema change.
- Typing/validation is enforced by the API layer against the registry (the DB
  does not type JSONB members).

## Soft delete (DB-S3)

- Every entity table: `deletedAt` timestamp (null = live) + `deletedBy`
  (userID). Records are never physically deleted (matches the UI standard).
- Every read path excludes deleted rows **by default** — a central
  query-builder rule, not per-endpoint code; explicit `includeDeleted` only
  for admin/restore surfaces.
- **All indexes and unique constraints are partial** (`WHERE deletedAt IS
  NULL`): live-row reads never pay for deleted rows, and re-adding a live
  duplicate of a deleted row does not collide with the corpse.
- Cascade behavior (does soft-deleting a parent soft-delete children?) is
  declared **per relationship** in the schema registry.

## Optimistic concurrency (DB-S4)

- Every entity table: `rowVersion` integer, incremented on every update.
- Reads that can lead to an edit carry the version out; every write carries
  it back (`WHERE …ID = ? AND rowVersion = ?`). Zero rows updated → the API
  returns **409 with the current record in the body** so the client can show
  merge/refresh, or auto-retry a field-level PATCH when its field is
  untouched in the fresh copy.
- No locks: readers never wait on writers; writers hold nothing across user
  think-time.
- Bulk/system writers (imports, jobs) use read-modify-write with retry —
  never blind overwrite. `customAttributes` versions at the record level.

## Audit & history (DB-S5)

- **Audit columns on every entity table** (API-maintained on every write):
  `createdAt`, `createdBy`, `modifiedAt`, `modifiedBy`. Grids sort/filter on
  these; `modifiedAt` is indexed on every table (it powers the change feed).
- **One system-wide `fieldChange` history table**: `fieldChangeID` (UUIDv7),
  `entityType`, `recordID`, `fieldName`, `oldValue`, `newValue`
  (text/JSONB), `changedAt`, `changedBy`. Written by the API whenever a
  **history-tracked** field changes; tracking is a per-field flag in the
  schema registry (and attributeDefinition) — status, assignments, capacity;
  not every keystroke in a notes field.
- A record's History panel is one indexed lookup (`entityType + recordID`).
  This is a display-grade audit trail — not a backup, not event sourcing;
  retention trimming is allowed later without schema change.

## The schema registry (DB-S6)

- **Every field of every entity — built-in or user-defined — has a registry
  row**: name (unique system-wide, mechanically enforced), type, label,
  required/validation rules, option source, history-tracked flag,
  visibility/grouping hints.
- Built-in fields' rows are seeded from source-controlled definitions **in
  the same migration that adds the column**; the build fails on drift (a
  startup registry-vs-actual-schema check). Custom fields' rows are created
  by admins at runtime.
- Served as one metadata endpoint (`GET /schema/{entity}`). It drives:
  server-driven UI rendering (grids/forms), API validation, duplicate
  detection config, history flags, export columns, and admin-SQL view
  columns. **One contract — the anti-enum-drift design.**

## Option lists are data, not schema (DB-S7)

- No database enum types, no CHECK-constraint value lists for choice fields.
- Shared tables: `optionSet` (the named list) + `optionValue`
  (`optionValueID` UUIDv7, `optionSetID`, `optionValueName`, display label,
  sort order, `activeFlag`). A field's registry row points at its option
  set; records store the `optionValueID`.
- Retiring a value = `activeFlag` off (hidden from new entry; historical
  records still render). Renaming a label = one row update, zero record
  touches. Sets are shareable across fields; custom "choice" attributes use
  the same tables.
- The dropdown and the validator read the same rows — the enum-drift bug
  class is impossible by construction.
- Admin UI must show which fields use an option set before allowing edits.
