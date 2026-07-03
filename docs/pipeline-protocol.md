# Pipeline protocol — the end-to-end test

What is being tested, how the phases run, and how the result is judged.
The orchestrator (main Claude Code session) MUST follow this; deviations are
themselves findings and go in `docs/pipeline-log.md`.

## What is being tested

Whether a defined agent team + skills can carry a real project through
**brief → requirements → design → build** such that:

1. Each phase artifact is usable by the next phase's agents **as written** —
   without the orchestrator or human rewriting it.
2. Traceability holds end-to-end: every `MENT-###` requirement is covered by
   design, implemented in code, and verified by a test (or explicitly deferred
   at a gate).
3. Human effort concentrates at the **gates** (review/approve/answer
   questions), not inside phases (fixing agent output).

## Ground rules (orchestrator discipline)

- **Never do an agent's job inline.** If the spec is missing something, re-run
  or message the requirements-analyst — don't patch the spec yourself. Every
  violation invalidates that phase's data point; log it.
- **Log every intervention** in `docs/pipeline-log.md`: date, phase, what the
  agent got wrong or needed, whether it was re-prompted / re-run / human-fixed.
  One line each. No intervention = no entry; an empty log is a result.
- **Gates are hard.** A phase starts only when Doug has approved the previous
  phase's artifact. Approval is recorded in the pipeline log.
- Questions agents raise for the stakeholder are collected and put to Doug at
  the gate — batched, not drip-fed.

## Phases

### Phase 0 — Define the pipeline (done at scaffold)
Agent definitions, skills, domain brief, this protocol.
**Gate 0:** Doug reviews the team + protocol.

### Phase 1 — Requirements
Run **requirements-analyst** with `docs/domain-brief.md`. It produces
`docs/requirements-spec.md` (per the `spec-authoring` skill) plus a list of
stakeholder questions. Doug answers the questions; the analyst revises.
**Gate 1:** Doug approves the spec (scope, MoSCoW priorities, out-of-scope list).

### Phase 2 — Design
Run **ux-designer** (→ `docs/ux-design.md`) and **solution-architect**
(→ `docs/technical-design.md`); the architect reads the UX design, so UX goes
first (or runs, then the architect gets its output). Both trace to MENT IDs.
**Gate 2:** Doug approves both. Any requirement neither design covers is a
Phase 2 failure finding.

### Phase 3 — Build
Iterative: **backend-developer** and **frontend-developer** implement per the
technical design (worktree isolation if run in parallel); **code-reviewer**
reviews every change-set before it lands; **qa-engineer** writes the test plan
from the spec (not the code) and implements tests. Build proceeds in vertical
slices (one user-visible capability at a time), each slice reviewed + tested.
**Gate 3:** the app runs end-to-end; QA's traceability matrix shows every
Must-have MENT ID verified; Doug exercises the app.

### Phase 4 — Evaluate the pipeline
Write `docs/pipeline-evaluation.md`: metrics below + a candid narrative of
where the pipeline held and where it leaked.

## Evaluation metrics

| Metric | Source |
|--------|--------|
| Interventions per phase (count + severity: re-prompt / re-run / human-fix) | pipeline-log |
| Traceability coverage: % MENT IDs with design / code / test links | QA matrix |
| Gate rework: artifacts approved as-is vs. sent back (and why) | pipeline-log |
| Cross-phase defects: build-phase problems whose root cause was a spec/design gap | code-reviewer + QA findings |
| Stakeholder question quality: questions that changed scope vs. noise | Gate 1 notes |

## Working agreements for all agents

Baked into the agent definitions; repeated here for the orchestrator's prompts:

- Load your named skill(s) before producing your artifact.
- Your artifact must stand alone — the next agent gets the file, not your
  conversation.
- Cite requirement IDs; never invent new requirements silently (raise them as
  proposed additions instead).
- State assumptions explicitly in a dedicated section; unresolved questions go
  in a "Questions for the stakeholder" section, not inline hedges.
