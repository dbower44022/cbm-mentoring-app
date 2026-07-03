---
name: ui-standards
description: Standard UI functionality for this app — the canonical list-view grid, page layout/shell, navigation model, editor behavior, modals, notices, and accessibility rules. Load before designing screens (ux-designer), building UI (frontend-developer), or reviewing UI slices (code-reviewer).
---

# UI standards

> **STATUS: DRAFT STRAWMAN — not approved.** Written from an inventory of the
> existing staff tools before the stakeholder's own standards were gathered.
> Do not treat as binding until Doug has revised and approved it; agents must
> not load-and-follow this skill while this banner is present.

The canonical behaviors every screen inherits. The UX design references these
components by name ("standard grid with columns …") instead of respecifying
them; deviations must be explicit in the design and justified. Distilled from
the production CBM staff tools (`cbm-client-intake` `/mentoradmin` and
`/assignments`), keeping the best variant wherever the two disagreed.

## App shell & layout

- Shell: `<main class="cbm-container">` → header (h1 + subtitle + signed-in-as
  + primary nav) → view content → `.cbm-footer` (© year, version + environment
  from `/healthz` via a shared footer script).
- Design tokens: reuse the CBM tokens (`tokens.css` vocabulary from
  cbm-client-intake — brand navy/gold, status colors, Roboto/Roboto-Slab, type
  scale, `.cbm-button`, `.cbm-field`). One copy, served shared; per-view CSS
  builds on the variables, never redefines the palette.
- Page archetypes — every screen is one of: **list view** (standard grid),
  **detail view** (read-only summary card + optional tabbed editor), **form
  view**, or **dashboard** (summary tiles + shortcuts). Anything else is a
  design-gate discussion.
- **Navigation is page-level, deep-linkable.** List → detail is a view swap
  with a hash route (`#/engagements/123`) so browser Back works and views are
  linkable/bookmarkable. (Delta from the existing tools, which have no
  routing.) Modals are reserved for: confirmations, blocking progress flows,
  and small read-only previews — never for a primary editing surface.
- Layout collapses to a single column at ≤ 640 px. No page-level horizontal
  scrolling ever; wide content scrolls inside its own container. Baseline bar:
  every screen must be *usable* on a phone (mentors will log sessions from
  one); full mobile-first design is a requirements question, not assumed here.

## The standard grid (list views)

Every list view is this component unless the design explicitly deviates.

**Data model** — load the full filtered dataset once, then search/filter/sort
**client-side** (right call at CBM scale: tens of mentors, hundreds of
engagements). Server-side filtering only for unbounded datasets (e.g. a
sessions log spanning years) — the technical design names those explicitly and
gives them server-side filter + pagination.

**Functionality checklist:**

- **Search** — one free-text box, debounced ~200 ms, matching a defined
  per-grid haystack (visible text columns + relevant hidden fields like
  expertise lists). Case-insensitive substring.
- **Filters** — a filter bar next to search: single-`<select>` per
  low-cardinality dimension (options derived from loaded data, sorted, with an
  "All" default); a checkbox-popover (`<details>`/`<summary>`, outside-click +
  Escape close) for multi-select dimensions like status; plain checkboxes for
  boolean predicates ("Has capacity"). All criteria AND-combined with search.
- **Sorting** — every column sortable client-side. Click toggles direction;
  switching columns applies the type default (text asc, numbers/dates desc).
  Header shows the indicator AND sets `aria-sort`. Each grid declares a
  default sort. Null-like values ("Unlimited", missing) sort with an explicit
  accessor, not by accident.
- **Count + refresh** — always show "Showing X of Y <things>" reflecting
  active filters, and a Refresh button that refetches. A grid a user returns
  to after editing reloads itself (dirty flag or route-change refetch).
- **Row rendering** — first/primary column is the record name as a link (hash
  route) to the detail view. Statuses render as **badges** (token status
  colors, one shared badge component). Emails are `mailto:` links. Numeric
  columns right-aligned; dates display as `YYYY-MM-DD`. Sticky header row.
- **Row actions** — inline per-row controls (button, or select + button)
  only for the row's primary action. Any state-changing action gets a
  **standard confirm modal**; during the request the row is busy-styled
  (dimmed, non-interactive) and the control disabled. No bulk selection
  unless a requirement demands it.
- **States** — loading ("Loading …"), empty (friendly, plain-language, says
  *why* it might be empty and what to do next), and error (inline notice
  naming which load failed — independent data sources report independently).
  Filter-to-zero shows the empty state with "no matches" wording, not a blank
  table.
- **Responsive** — the grid scrolls horizontally inside its own container on
  narrow screens; columns get `min-width`s so cells never wrap into mush.

## Detail & editor

- Detail view = toolbar (Back + primary actions) → title → notice line →
  read-only **summary card** (key facts, computed values) → **tabbed editor**
  when the record is editable (tabs by field group; all panels stay in the DOM
  so Save reads across tabs).
- Field rendering by type: enum→select (blank "(none)" option; a stored value
  that has drifted out of the option list stays selectable so it isn't
  silently lost), multiEnum→checkbox grid, bool→checkbox with inline label,
  int→number, date→date input, long text→textarea, rich text→sanitized
  contenteditable with a minimal toolbar.
- **Dirty-diff save:** snapshot each field's normalized value at render; Save
  sends only changed fields; re-baseline after success. Save button disabled
  while in flight; success/error as an inline notice.
- **Unsaved-changes guard:** leaving an editor with dirty fields (Back,
  route change, or tab/window close via `beforeunload`) prompts with the list
  of changed field *labels* — "Keep editing" (focused) vs "Discard changes".
- Pre-save validation issues use the standard confirm modal ("Save anyway?" /
  Cancel), and Cancel focuses the first offending field (switching to its tab).

## Shared components & behaviors

- **One confirm modal** component (title, body/items, cancel + confirm
  labels): overlay + card, Escape and backdrop-click cancel, initial focus on
  the **safe** (non-destructive) action, `role=dialog aria-modal=true`, focus
  returned to the invoking control on close. The existing tools each hand-roll
  their own — this app gets exactly one.
- **One HTML sanitizer** — the strict allowlist kind (DOMParser, allowed tags
  only, strip `on*`/`javascript:`, external links `rel=noopener
  target=_blank`) — used for both rendering and editor-loaded content.
- **Notices, not toasts** — inline `.notice` banners (success/error variants)
  in a consistent slot per view, scrolled into view on set, cleared on the
  next action.
- **Auth UX** — dedicated login view; boot checks `/session` (401 → login
  silently; network/5xx → "server isn't responding"); every API caller treats
  401 as session-expired → show login (preserving the attempted route to
  return to); logout always lands on login.
- **Keyboard & a11y** — focus starts in the first meaningful field/control on
  view change; logical tab order (decorative links excluded); visible focus
  everywhere; buttons are `<button>`, links navigate; tables use real
  `<th scope=col>`; `aria-sort` on sorted headers; modals per above. Grids and
  forms fully operable without a mouse.

## Review hooks (for code-reviewer)

Findings, not taste: a second confirm-modal implementation; a grid missing
count/empty/loading/error states or `aria-sort`; a save that sends unchanged
fields; a status rendered as bare text instead of a badge; palette values
hard-coded instead of tokens; a modal used as a primary editing surface;
page-level horizontal scroll.
