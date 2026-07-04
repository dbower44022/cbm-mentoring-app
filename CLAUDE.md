# CLAUDE.md — CBM Mentoring Application (mentorapp)

Agent bootstrap for this repository. This file is operating bootstrap, not
documentation: per the engagement's coding standards, **no markdown
documentation lives in this repo** — module/platform documentation, decisions,
requirements, and standards live in the CRMBuilder V2 store (engagement
ENG-004) and are read from there.

> Process note: this file described the earlier repo-local subagent pipeline
> (MENT-### IDs, `.claude/agents/`). That process was replaced 2026-07-03/04
> by the DB-native CRMBuilder pipeline — requirements, planning items,
> releases, and work tasks live in the store, and the ADO runtime builds
> here. The old text is in git history; `docs/` and `prompts/` are historical
> working artifacts of the engagement, not app documentation.

## What this is

The CBM Mentoring Application: a mentor-facing web app over the CBM CRM
system of record. Built release-scoped under CRMBuilder governance
(engagement ENG-004; release/PI/work-task records in the cloud store at
https://api.crmbuilder.ai, header `X-Engagement: ENG-004`). Current release:
CBM Mentoring App v1 (r2) — 86 confirmed requirements, ten planning items,
190 work tasks.

## Binding standards (in the store, composed into agent contracts)

The ENG-004 instruction skills bound to your agent profile ARE the standards:
UI, data model / API contract / read surface, observability & analytics,
code maintainability, testing & change discipline. Non-negotiables enforced
in review and CI:

- Strict typing; ruff format + lint at zero warnings; CI green before merge.
- Engines + configuration: never re-implement engine capability in features;
  one canonical home per concept; no dead/commented-out code.
- Structured JSON logging to stdout via `mentorapp.observability.get_logger`
  ONLY — no bare prints, no swallowed exceptions.
- Every response speaks the `{data, meta, errors}` envelope.
- Comments carry the WHY; contract docstrings on public surfaces; no orphan
  TODOs (every TODO names a PI or finding).
- Every code commit carries `Governed-By: PI-NNN` (an ENG-004 planning item
  in an executable state). Hook: `.githooks/` (warn mode);
  `git config core.hooksPath .githooks` after clone.

## Commands

```bash
uv sync            # install
uv run pytest      # tests (plain test root: tests/)
uv run ruff check src tests && uv run ruff format --check src tests
uv run uvicorn mentorapp.main:app --reload   # run locally
```

## Layout

`src/mentorapp/` application code; `tests/` plain pytest root (the ADO
affected-test gate runs the full `tests/` suite for any non-doc change —
per-repo plain mode).
