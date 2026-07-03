# Session prompt — UI standards, part 2 (remaining topics)

Paste or reference this file to start the session. Project context loads
from `CLAUDE.md`; this file scopes the session.

## Where we are

The UI standards for **grids** and **overall layout** are complete —
dictated by Doug, refined through a one-at-a-time suggestions round, and
consolidated into `.claude/skills/ui-standards/` (SKILL.md +
references/grid-standard.md + references/layout-standard.md). The full
decision record is `docs/ui-standards-discussion.md`. Read the SKILL.md
core principles before starting; they constrain everything below.

## Working method (this worked — keep it)

1. Doug dictates a topic; Claude captures decisions verbatim-faithfully in
   `docs/ui-standards-discussion.md` as they land (commit at checkpoints).
2. Claude asks targeted clarifying questions — a few at a time, never a
   wall; Claude does NOT author standards from its own judgment.
3. When Doug's dictation on a topic is done, Claude offers a **suggestions
   round**: proposals presented ONE AT A TIME, each with rationale + cost,
   Doug rules in/out/modified on each.
4. Consolidate the topic into the ui-standards skill references only after
   Doug's decisions are complete; new reference file per major area.

## Agenda — remaining UI topics

1. **Forms & edit screens.** The edit screen the Edit action opens, and the
   **per-field edit window** (double-click a read-only element). To define:
   form layout rules, field types, validation display (educate voice),
   required-field convention, save/cancel semantics (ties to existing
   rules: dirty guards, optimistic concurrency, same-user sync).
2. **Workprocess wizards.** UI standard for admin-defined wizards: step
   navigation/progress, validation per step, partial save/resume, cancel
   semantics, where results land (notifications for >10s, per the
   background-task rule).
3. **The Help system.** What backs "situation-specific help" (last item in
   every menu, app-wide): content model (admin-authored per
   panel/action/field?), presentation (panel? popup?), authoring/ownership.
4. **Look & feel.** The curated row-theme set's ground rules, app-wide
   typography/density scale, CBM branding (navy/gold tokens from
   cbm-client-intake?) — plus conditional-formatting capabilities inside
   themes.
5. **Loose end:** preview-pane dock position (right vs bottom vs user
   choice) — currently implementer's choice; rule if Doug cares.

## After UI standards conclude

The next area Doug named: **database/API agent rules** — standards/skills
for the data layer and API design (the same treatment the UI got). Expect
interactions with decided UI standards: admin-authored SQL data sources +
per-source permission grants, server-side search/count/aggregates,
infinite-scroll caching, optimistic concurrency, soft deletes everywhere,
same-user sync (BroadcastChannel-class), designed-for-later server push,
background tasks, view/pin/preference persistence.
