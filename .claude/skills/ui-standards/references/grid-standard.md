# The grid standard

Every list view is this grid. Approved by Doug 2026-07-03 (source:
`docs/ui-standards-discussion.md`). Nothing here is optional unless marked as
a view setting or implementer's choice.

## Anatomy

Three stacked regions, always: **action bar** (top) → **grid** → **status
bar** (bottom).

## The view system

A grid's entire presentation is driven by its **view**.

- **Data source** — a system-defined query with full SQL power (joins,
  filters, calculated fields). **Authored by system administrators only**
  (visual query builder AND raw SQL); all users consume. **Access is granted
  per data source** — that is the security boundary; sources frequently
  include userID filtering to scope rows to the requesting user.
- **A view defines:** its data source; which fields display; column order;
  column width (min/max in px or %, or smart sizing — fit to the max width of
  ~90% of the data, ignoring outliers); column format; grouping (multiple
  nested levels from the data set; optional tree rendering; default
  collapsed/expanded is a view setting); row theme (curated set — height,
  colors, font; **conditional formatting lives in the theme**); whether
  ad-hoc column filters are allowed.
- **System views are read-only**; open → modify → save as a user view. Users
  can update/rename/delete/save their own views. **User views are not
  shareable**; an admin can copy a user view and promote it to a system view.
- A grid **always opens on the view last displayed** on that list page (per
  user, persisted long-term).
- **Action bar left:** the view selector (system + user views; selection
  applies instantly) + a view-edit button opening the full view-settings
  form. Modified-but-unsaved settings apply temporarily (until another view
  is selected) and the selector shows a **modified indicator**; temporary
  changes are saveable as a user view.

## Scrolling & server-side truth

- **No pagination, ever.** All grids infinite-scroll with cache optimization.
- **Server-side truth:** full-text search, the status-bar row count, and ALL
  aggregates are computed server-side over the **entire filtered result
  set** — the loaded window affects only what's rendered, never what's
  counted, matched, or summed.

## Search (action bar middle)

- Live search from the **3rd character**; **per-grid history of the last 5
  searches**; scope = **displayed columns only**; always **narrows** the
  view's results (never replaces view filters).

## Actions (action bar right + context menu)

- Every data set ships **standard CRUD actions** (plus **Export** and
  **Print**) plus admin-defined functions and **workprocesses** (custom
  wizards).
- Action bar right: the data set's **two most common actions** as buttons +
  an **"Other Actions"** dropdown listing ALL actions (the two common ones
  first). **Right-click on the grid opens the same full menu.** The **last
  item in every menu is Help** (situation-specific — app-wide rule).
- Actions **act on the selected rows**.
- **Never hide, never disable/gray out an action.** Every action is always
  visible and invocable by any user with data-set access; an invalid
  invocation (no selection, multi not allowed, invalid selection, missing
  permission) produces a **detailed explanation** of why it can't run.
  To restrict functionality for a group, create a similar data set with
  tighter permissions — never per-action hiding.
- **Classification (declared per action):** safe (no confirm) / modifying
  (confirm optional) / **destructive (confirm required)** — the confirmation
  names the action, the exact count, the affected records (first N + "and X
  more"), and calls out selected-but-filtered-out rows. One shared
  confirmation component app-wide. Large selections always warn "performing
  on X items."
- **Soft deletes system-wide:** records are never physically deleted.
  Confirmation wording must be honest — "removed from all views; an
  administrator can restore it," not "cannot be undone."

## Selection & clicks

- Single left-click selects (deselecting others). Shift/Ctrl-click enters
  multi-select and **reveals a checkbox column**. **Select-all selects the
  ENTIRE filtered result set** (no "visible rows first" two-step).
- Filter/search changes **keep the selection, with notice** ("10 selected, 3
  not in current filter"); actions' confirmations spell out hidden-selected
  rows. Never silently deselect.
- **Double-click = open/view** the record. Right-click = actions menu.

## Keyboard (full no-mouse operation)

Arrow ↑/↓ = row focus (visible indicator; auto-loads at the bottom edge);
Space = toggle selection; Shift+arrows = extend; Ctrl+A = select all (entire
set); Enter = open/view; Menu key / Shift+F10 = actions menu; focus starts in
the search box and `/` returns to it; column-header focus + Enter sorts.

## Sorting & columns

- **Multi-column sorting:** click = sole sort (repeat toggles direction);
  Shift+click adds secondary/tertiary (repeat toggles; third click removes).
  Sorted headers show a direction arrow **+ numbered badge** (1, 2, 3…).
  Header sorting is a temporary view modification (modified indicator,
  saveable).
- Users can resize columns. **Smart resize:** on wide screens columns
  auto-expand to show maximum data. **Rule: NO WHITE SPACE in a grid**
  (unless there is too little data to fill it).
- **Ad-hoc column filters** (view setting, off by default in spirit —
  well-designed saved views are the culture, ad-hoc filters the escape
  hatch): header funnel → distinct-value checkboxes / numeric-date ranges /
  text contains; filled-funnel indicator; ANDs with view filters + search;
  marks the view modified; distinct values come from the server-side
  filtered set.

## Aggregates

Group rows carry group aggregates; a **footer row inside the grid** carries
result-set aggregates. Both server-side over the full filtered set.

## Status bar

- **Right:** row count of the filtered set — "200 rows"; with multi-select,
  "200 rows, 10 Selected" (plus the keep-with-notice variant).
- **Middle:** action progress/completion messages ("Loading grid",
  "Recalculating Averages") + a **progress bar with estimate** whenever an
  action exceeds a few seconds.
- Remaining space: reserved for situation-specific uses (TBD as the app
  develops).

## State restoration

Returning to a grid from a record restores it **exactly**: view + temporary
modifications, search text, scroll position, selection, focused row — while
the **data refreshes** underneath (edited rows show new values;
keep-with-notice applies). Scroll/selection/search restoration is
**session-only**; the view choice persists long-term.

## Export & Print (standard actions on every data set)

- **Export** the current view *as the user sees it* (columns, order,
  formats, sort, filters + search): selection if any, else the entire
  filtered set (server-side). CSV + Excel (.xlsx). **Formatted values by
  default, with an "export raw values" checkbox.** Long exports = background
  task with progress. Grouping: flat with group columns or Excel grouping
  (implementer's choice, v1).
- **Print:** same scope semantics; print-friendly rendering of the current
  view; details at design time.

## Deep links

The URL identifies **grid + active view** only (search/sort/selection are
session state). Links are references — data-source permission still applies;
a link to another user's private view falls back to the recipient's
last-used view with an educate-voice notice.

## Empty & error states (educate voice: what happened → why → what next)

Four distinguished states: **view-empty** (states the view's criteria),
**filtered-to-zero** ("no rows match 'acme' — 200 rows hidden [Clear
search]"; never let a filter masquerade as missing data), **data-source
error** (plain words + [Retry]; detail available, not dumped),
**no-access** (which permission is missing, who grants it).

## Explicitly out (v1)

**Inline cell editing** — out for v1, revisit later (likely as a view
setting); the action set + multi-select actions are expected to make it
unnecessary.

## Context-menu anchoring (ruled by Doug 2026-07-07)

- The grid's RIGHT-CLICK menu opens AT THE CURSOR — the whole point of
  right-click is zero mouse travel to the action. Same full action list as
  Other Actions; only the anchor differs (button menus anchor to the
  button). Clamp to the viewport; never anchor a context menu to a fixed
  corner.

## Column sizing integrity + selection hygiene (ruled by Doug 2026-07-07)

- ALL columns are user-resizable (drag the header boundary). Every column
  carries a MINIMUM width in CHARACTERS that neither smart auto-sizing nor
  a user drag may violate.
- When the window is too small to honor every minimum, the grid scrolls
  HORIZONTALLY inside its region — columns are never squashed below minimum.
- A minimum, THEME-based cell border/gutter size keeps adjacent cells' text
  from running together (the gutter is a theme token, not per-view styling).
- Shift-click extension highlights ROWS ONLY: native browser text selection
  never activates across the range.

## Column format defaults by data type (ruled by Doug 2026-07-07)

- Every column has a FORMAT setting defaulted by data type, overridable in
  the user's view settings.
- Default justification: text LEFT, numbers CENTER (explicit ruling — beats
  the right-align convention), dates LEFT, status LEFT.
- Resizing a column NEVER triggers its sort (Doug clarification 2026-07-07):
  the drag gesture and the sort click are distinct.
