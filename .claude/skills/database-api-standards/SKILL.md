---
name: database-api-standards
description: Doug-approved database and API standards for the CBM Mentoring Custom App (ENG-004) — data model, API contract, read surface & platform services. Load before designing or building any schema, storage, or API code.
---

# Database & API standards — CBM Mentoring Custom App

Approved by Doug 2026-07-03. Decision record:
`docs/database-api-standards-discussion.md`. The V2 database records are the
source of truth (ENG-004 instruction skills); these files are authoring
artifacts — edits here must be re-recorded to the DB.

## Doug's anchor requirements (non-negotiable)

1. **GUIDs for all unique IDs** — never auto-increment integers.
2. **Field names are unique across all tables** — no generic reused names
   (no `name` column on every table). Includes keys: primary keys are
   entity-named (`mentorID` on Mentor) and a foreign key carries the
   identical name as the primary key it references.
3. **User-defined attributes** — admins add fields without a developer
   schema change.
4. **Read performance over write performance** — the system is heavily
   read/lookup-skewed; every design choice favors reads.

## The three standards

- `references/data-model-standard.md` — identifiers, naming, custom
  attributes, soft delete, optimistic concurrency, audit & history, the
  schema registry, option lists.
- `references/api-contract-standard.md` — list reads (keyset pagination,
  server-side search, counts), the write contract (per-field PATCH,
  envelope, structured errors, duplicate detection), the change feed /
  sync, the preferences API.
- `references/platform-services-standard.md` — generated read views +
  admin-authored SQL data sources, background jobs & artifacts,
  parsing/normalization services.
