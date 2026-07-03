---
name: spec-authoring
description: Requirement-spec format for this project — MENT IDs, acceptance criteria, MoSCoW, traceability rules. Load before writing or revising docs/requirements-spec.md, and when any other artifact needs to reference requirements correctly.
---

# Spec authoring

Every capability in this project traces to a requirement. The formats below are
mandatory — downstream agents parse them.

## Requirement format

```markdown
### MENT-014 — Mentor logs a session
**Priority:** Must  |  **Actor:** Mentor  |  **Status:** Proposed

A mentor records a completed session against one of their active engagements:
date, duration, format (in person / video / phone), topics, private notes,
next steps.

**Acceptance criteria:**
- AC1: A session cannot be logged against an engagement the mentor is not
  assigned to.
- AC2: Date may not be in the future; duration is 15–480 minutes.
- AC3: The engagement's session count and last-session date update immediately.

**Source:** domain-brief §4; stakeholder answer 2026-07-xx.
```

Rules:

- **IDs are permanent.** `MENT-###`, assigned sequentially, never reused or
  renumbered — a dropped requirement keeps its ID with status `Rejected` or
  `Deferred` and one line of rationale.
- **Priority is MoSCoW** (Must / Should / Could / Won't-this-release). Gate 3
  only requires Must-haves verified.
- **Acceptance criteria are testable** — concrete, observable, no "user-friendly",
  no "fast". Each AC is individually verifiable; QA writes tests against them
  by number (`MENT-014/AC2`).
- **Every requirement names its source** (brief section, stakeholder answer, or
  "analyst-proposed" — flagged for the gate).
- One requirement = one capability. If an AC list needs "and also…", split it.

## Spec document structure

1. Purpose & scope (incl. explicit **Out of scope** list — as load-bearing as
   the requirements)
2. Actors & definitions
3. Functional requirements, grouped by actor journey
4. Non-functional requirements (same format; NFR acceptance criteria must
   still be checkable — e.g. "boots with zero env vars", not "secure")
5. Assumptions
6. Questions for the stakeholder (numbered, each with why it matters and the
   analyst's recommended answer)

## Referencing requirements downstream

Design docs, code, and tests cite bare IDs: `MENT-014`. A design element,
endpoint, or test that satisfies no requirement is scope creep — either
propose a new requirement (status `Proposed`, decided at the next gate) or
delete the element. Never silently add an ID to the spec of a phase already
approved.
