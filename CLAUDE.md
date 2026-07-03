# CLAUDE.md

Guidance for Claude Code working in the **cbm-mentoring-app** repository.
This file is the recovery anchor if a session is lost — keep the
"Current status" section up to date.

## What this is

An **end-to-end test of an agent-driven software development pipeline**, using a
real workload: specifying and building a custom web application for the
**mentoring process** of Cleveland Business Mentors (CBM).

Two deliverables, in priority order:

1. **The pipeline evaluation** — can a defined team of Claude Code subagents
   (`.claude/agents/`) with web-app-development skills (`.claude/skills/`) carry
   a project from domain brief → requirements spec → UX + technical design →
   working build, with human review only at phase gates?
2. **The application itself** — treated as a real candidate for CBM's mentoring
   process (not a throwaway), so the quality bar at every phase is "could ship".

The process being modeled is the CBM mentoring lifecycle; the seed knowledge is
`docs/domain-brief.md`. The pipeline rules, phases, gates, and evaluation
criteria are in `docs/pipeline-protocol.md` — **read it before running any
agent**.

## How work happens here

- The main session acts as **delivery lead / orchestrator**: it launches the
  subagents, enforces the phase gates, and never does an agent's job inline
  (that would invalidate the test — see the protocol's ground rules).
- Each phase ends at a **human review gate**: Doug approves the phase artifact
  before the next phase starts. Do not start a phase whose predecessor is
  unapproved.
- Every requirement gets an ID (`MENT-###`); design, code, and tests must trace
  back to requirement IDs. The skills enforce the formats.
- Pipeline friction (an agent's output needing human/orchestrator correction,
  rework loops, ambiguities) is **data, not embarrassment** — log every
  intervention in `docs/pipeline-log.md` as it happens.

## The agent team (`.claude/agents/`)

| Agent | Phase | Produces |
|-------|-------|----------|
| requirements-analyst | 1 Requirements | `docs/requirements-spec.md` |
| ux-designer | 2 Design | `docs/ux-design.md` |
| solution-architect | 2 Design | `docs/technical-design.md` |
| backend-developer | 3 Build | server code + API |
| frontend-developer | 3 Build | UI code |
| code-reviewer | 3 Build | review findings (gates merges) |
| qa-engineer | 3 Build | test plan + tests, traced to MENT IDs |

Skills: `webapp-standards` (house stack + conventions), `spec-authoring`
(requirement format + traceability), `design-doc-standards` (design doc
formats), `ui-standards` (canonical list-view grid, layout/navigation model,
editor + modal + notice behaviors — distilled from the production staff tools,
inconsistencies resolved). Agents are told to load the relevant skill before
producing their artifact.

## Relationship to other CBM repos

- **`cbm-client-intake`** (sibling) — the production intake forms + staff tools
  (`/mentoradmin`, `/assignments`) against EspoCRM. This project may read it for
  domain reference; **never modify it from this project.**
- **`dbower44022/ClevelandBusinessMentoring`** (crmbuilder) — governed process
  definitions (MN-*). Read-only reference; its governance applies if you ever
  touch it (read its CLAUDE.md first).
- EspoCRM (crm-test / prod) is CBM's system of record today. Whether and how
  the new app integrates with it is a **design decision for the agents**, not a
  given — the domain brief states the constraint honestly.

## Current status (updated 2026-07-03)

**Phase 0 — pipeline defined. Scaffolded 2026-07-03; awaiting Doug's review of
the agent team + protocol before Phase 1 runs.**

- Repo scaffolded: agent definitions, skills, domain brief, pipeline protocol.
- Defaults chosen while Doug was AFK (confirm or redirect): new sibling repo;
  app is a real candidate (pipeline test first); agents seeded from the domain
  brief (not fresh elicitation); Phase 1 (requirements) runs only after review.
- Nothing has been run yet: no agent has executed, no spec/design/code exists.

**Next:** Doug reviews Phase 0 → run requirements-analyst (Phase 1) →
gate review → Phase 2 (ux-designer + solution-architect).

## Commands

Nothing to run yet — Phase 3 (build) will establish the toolchain per the
architect's design (house default: uv + FastAPI + pytest, see
`webapp-standards`).

## Conventions

- **Push convention:** Claude commits in this local clone; **Doug reviews and
  pushes** (no remote configured yet). Do not push without being asked.
- Conventional Commits (`feat:`, `docs:`, `chore:`, …).
- Never commit secrets; the app must boot with zero env vars (dry-run/dev mode).
