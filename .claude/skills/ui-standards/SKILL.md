---
name: ui-standards
description: The stakeholder-approved UI standards for this app — core design principles, the canonical list-view grid, and the panel/layout/window model. Load before designing screens (ux-designer), building UI (frontend-developer), or reviewing UI slices (code-reviewer); read the reference file(s) relevant to your slice.
---

# UI standards

**Status: APPROVED by Doug (stakeholder) 2026-07-03 for grids and overall
layout.** Decision record: `docs/ui-standards-discussion.md`. Remaining
topics (listed at the bottom) are NOT yet defined — do not invent standards
for them; raise them instead.

## Core principles (app-wide, non-negotiable)

1. **Educate, never hide.** No action is ever hidden or disabled/grayed out.
   Every action is visible and invocable by any user with data-set access;
   an invalid invocation (selection, state, permission) produces a detailed
   explanation of why it can't run and what to do next. Empty states,
   errors, broken links, and conflicts all use the same voice: what
   happened → why → what next.
2. **Views are the culture.** Users shape their own workspaces: admin-built
   data sources → saved views → pinned tabs/dashlets. Repeated tasks
   deserve an optimal, user-defined UI. Ad-hoc manipulation exists but
   saved views are the primary tool.
3. **Data-dense, never crowded.** White space is a waste. Panels and
   dialogs scale to full screen and show as much data as possible; grids
   auto-size to eliminate white space.
4. **Read and edit are separate modes.** Previews are read-optimized with
   no edit controls; editing is an explicit act (Edit action or per-field
   double-click).
5. **Server-side truth.** Counts, search matches, and aggregates always
   reflect the entire filtered result set, never the loaded window.
6. **Never lose user work or selection silently.** Dirty-window guards,
   keep-selection-with-notice, optimistic concurrency with visible conflict
   resolution, soft deletes system-wide (no record is ever physically
   deleted — and confirmation wording is honest about that).
7. **Help is everywhere.** The last item in every menu is Help,
   situation-specific. (The Help system's content model is a pending topic.)
8. **Desktop and web first.** Phone support comes later as a separate app;
   never compromise this UI for small screens.

## The standards

- **`references/grid-standard.md`** — every list view: the action bar /
  grid / status bar anatomy, the view system (data sources, views, themes),
  infinite scroll, search, actions & the never-hide rule mechanics,
  selection & keyboard, multi-sort, columns, aggregates, status bar, state
  restoration, export/print, deep links, empty/error states.
- **`references/layout-standard.md`** — the panel model, navigation
  (tabs/menu/tree + pins + Ctrl+K palette + broken-pin fallbacks), Home &
  messaging, dashlets, preview/pop-out windows, cross-window sync &
  editing rules, session lifecycle, background tasks & notifications, the
  standard header.

Read the reference for the surface you're working on; both if the slice
spans them. Wireframes and designs cite these components by name (e.g.
"standard grid: columns …, default view …, empty state: '…'") and specify
only per-screen particulars — never respecify standard behavior, and mark
any deviation explicitly for the design gate.

## Pending topics (no standard yet — do not improvise)

- Forms & edit screens (layout, validation display, save/cancel, the
  per-field edit window's details).
- Workprocess wizards (step navigation, partial save, cancel semantics).
- The Help system (what backs "situation-specific help").
- Look & feel: the curated theme set's ground rules, typography/density
  scale, CBM branding.
- Preview-pane dock position (right vs bottom) — implementer's choice
  unless ruled at design time.

# Principle 0 — Conventions baseline (ruled IN by Doug 2026-07-07)

You are an expert UI designer. Wherever these standards and the confirmed
requirements are silent, the UI follows the most common desktop-web
conventions — context menus open at the cursor, Escape dismisses transient
surfaces, double-click opens, Enter activates the focused control, drag
affordances look draggable, and so on. Precedence when sources conflict:
(1) Doug's rulings and confirmed requirements, (2) these standards skills,
(3) common conventions, (4) designer judgment. A deliberate deviation from
common convention is never made silently — it is flagged for the review
gate with its reason. Conventions are enforced by the rendered-verification
gate: authoring to convention does not exempt the render from inspection.
