# cbm-mentoring-app

An end-to-end test of an agent-driven software development pipeline, using a
real workload: specifying and building a custom web application for the
mentoring process of Cleveland Business Mentors (CBM).

- **What's being tested:** whether a defined team of Claude Code subagents
  (`.claude/agents/`) with shared skills (`.claude/skills/`) can carry a
  project from domain brief → requirements → design → working build, with
  human review only at phase gates. Protocol + metrics:
  `docs/pipeline-protocol.md`.
- **The workload:** the post-assignment mentoring engagement process (session
  logging, acceptance, progress, outcomes) — the part of CBM's lifecycle with
  no software support today. Domain input: `docs/domain-brief.md`.
- **Operating guide for Claude Code sessions:** `CLAUDE.md`.

Current phase: **0 — pipeline defined, awaiting review** (see CLAUDE.md
"Current status").
