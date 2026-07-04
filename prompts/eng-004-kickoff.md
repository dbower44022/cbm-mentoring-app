# Session prompt — ENG-004 CBM Mentoring Custom App (CRMBuilder E2E)

Start this session **in `~/Dropbox/Projects/crmbuilder`** (its CLAUDE.md must
govern). This is CRMBuilder engagement **ENG-004 "CBM Mentoring Custom App"**
(active) — the end-to-end test of the CRMBuilder system: specify and build
CBM's custom mentoring application through the governed process, with agents
and skills defined in the V2 registry.

## Session bootstrap (mandatory, per crmbuilder CLAUDE.md)

Cloud API `https://api.crmbuilder.ai`, credentials in
`crmbuilder-v2/data/crmbuilder.env`, header `X-Engagement: ENG-004`. Read at
start: TOP-013 recording rules + children; active governance-rules;
preferences; reference-pointers. Requirement-first: confirmed REQ + PI before
any build; `Governed-By: PI-NNN` commit trailers; no new terminology without
Doug's approval.

## Context established 2026-07-03 (session in cbm-client-intake root)

- **The agent/skill model is CRMBuilder's own:** `PRDs/product/NEW-Master
  PRDs/Agent PRDs/Agent-System-Target-Model.md` (D1–D16; DEC-677 capability-
  description agent lookup; DEC-692 task observability). Agents = registry
  records, `<Area> <Tier> Agent` (Architect/Developer/Tester per build area);
  an Agent task's contract input = system ∪ engagement skills + governance
  rules + learnings, version-stamped. Skills registry: 110 system-scoped
  skills (kind=tool|instruction); **PRJ-079 built a SKILL.md→registry
  importer** (idempotent by name). `/agents` returned 0 rows via the API —
  verify where the agent registry actually lives (PRJ-066/069 built its
  config UI + runtime wiring) before defining agents.
- **Approved UI standards exist** (Doug-dictated, 2026-07-03): grids +
  overall layout, in `~/Dropbox/Projects/cbm-mentoring-app/` —
  `docs/ui-standards-discussion.md` (decision record) and
  `.claude/skills/ui-standards/` (SKILL.md + references/grid-standard.md +
  references/layout-standard.md — authored in importable SKILL.md form).
  UI topics still undefined: forms/edit screens, workprocess wizards, Help
  system, look & feel (see that repo's `prompts/ui-standards-session-2.md`).
  Next standards area Doug named: **database/API rules**.
- **Input PRDs:** `~/Dropbox/Projects/cbm-custom-mentor-app/prds/` (L1
  vision/architectural commitments + L2s: identity/actor model,
  authorization, CRM integration; May 2026). Doug: take as INPUT — this is a
  fresh end-to-end. Domain brief: `cbm-mentoring-app/docs/domain-brief.md`;
  production context: `cbm-client-intake` (intake forms + staff tools live
  against EspoCRM).

## DONE 2026-07-03 (registry entry criterion met)

- Agent registry located: **`/agent-profiles`** (AGP-NNN). 38 standing
  system profiles cover the webapp areas — ui (AGP-016/017/018),
  api (AGP-010/011/012), storage (AGP-001/002/006), espo (AGP-025/026/027).
  **Decision: reuse system agents via the engagement-overlay model; no
  ENG-004 profiles created** (create one only if a genuinely new area
  emerges).
- UI standards are IN THE DATABASE as ENG-004-scoped instruction skills:
  **SKL-111** (UI core principles), **SKL-112** (grid standard), **SKL-113**
  (layout & window standard) — each bound via `agent_profile_has_skill` to
  AGP-016/017/018. **VERIFIED:** `GET /agent-profiles/AGP-017/contract
  ?engagement=ENG-004` composes them into the system prompt; resolved for
  ENG-001 they are absent (scope isolation confirmed).
- Skill-content source remains `~/Dropbox/Projects/cbm-mentoring-app/
  .claude/skills/ui-standards/` — future edits must be re-recorded to the
  DB records (the DB is the source of truth; the files are authoring
  artifacts).

The registry setup is governance-recorded as **DEC-001 (ENG-004, Active,
2026-07-03)** — reuse of system agent profiles + the three skills/bindings +
DB-first recording of future standards. (Vocab note: decision status is
`Active`, not `accepted`.)

**UI STANDARDS COMPLETE (2026-07-03):** all topics dictated, ruled, and in
the DB as ENG-004 instruction skills bound to AGP-016/017/018 —
**SKL-111** UI core principles, **SKL-112** grid, **SKL-113** layout &
window, **SKL-114** forms & edit screens, **SKL-115** workprocess
(custom-app plug-in model), **SKL-116** help system, **SKL-117** look &
feel. UI Developer contract verified carrying all of them. Decision record:
`~/Dropbox/Projects/cbm-mentoring-app/docs/ui-standards-discussion.md`
(scratch; DB is truth). `prompts/ui-standards-session-2.md` is now
historical.

## DATABASE/API STANDARDS COMPLETE (2026-07-03)

Working method flipped BY DOUG for this area: he set four anchor
requirements (GUIDs/UUIDv7; field names unique across ALL tables incl.
entity-named PK/FK — `mentorID` everywhere, no bare `id`/`name`;
user-defined attributes; read-over-write priority) and asked Claude to
bring suggestions — 13 presented one at a time, ALL ruled IN. In the DB as
ENG-004 instruction skills bound to BOTH the storage (AGP-001/002/006) and
api (AGP-010/011/012) triads: **SKL-118** data model standard, **SKL-119**
API contract standard, **SKL-120** read surface & platform services
standard (edges REF-0022..0039). Governance: **DEC-002 (ENG-004, Active)**.
All six contracts verified carrying all three; ENG-001 isolation confirmed.
Decision record: `docs/database-api-standards-discussion.md`; authoring
artifacts: `.claude/skills/database-api-standards/` (DB is truth).

## Agenda for the next session

1. Bootstrap per the crmbuilder CLAUDE.md.
2. **Requirements capture** for the app itself: REQs under ENG-004
   (sources: the dictated UI + database/API standards, domain brief,
   cbm-custom-mentor-app L1/L2 PRDs) → confirm → PIs → compose + freeze a
   release → the pipeline run IS the E2E test.

## Working method (Doug's, proven 2026-07-03)

Doug dictates → capture decisions faithfully as they land → targeted
clarifying questions, few at a time → suggestions round presented ONE AT A
TIME (rationale + cost; Doug rules in/out/modified) → consolidate only after
his decisions are complete. Never author standards from Claude's judgment;
never present drafts as done.
