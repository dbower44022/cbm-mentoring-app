# UI standards — stakeholder decisions (working notes)

Captured live from discussion with Doug, 2026-07-03. These are HIS decisions;
they supersede the DRAFT strawman in `.claude/skills/ui-standards/SKILL.md`,
which will be rewritten from these notes once the discussion concludes.

## Grid anatomy (decided)

Every grid, no exceptions, is three stacked regions:

1. **Action bar** — above the grid.
2. **Grid** — the data table itself.
3. **Status bar** — below the grid; displays information appropriate to the
   situation (contextual).

## Action bar (in progress — dictated left to right)

- **Far left: view selector** — a dropdown list box choosing the grid's
  current *view*. Contains **system-defined views** and **user-defined
  views**. Selecting a view immediately re-displays the grid per the view's
  definition.
- **Next to it: view edit button** — opens a detail form exposing **all view
  settings** for modification. From there the user can either:
  - **save** the modified settings as a **new user-defined view**, or
  - **apply temporarily** — settings take effect until another view is
    selected or a new grid view is displayed.

## What a view defines (decided)

In order:

1. **Data source** — a system- or user-defined **query**, with the power of
   SQL: joins across tables, filters, calculated fields, etc. Selecting/
   creating the data source determines the list of fields available to the
   view.
2. **Field selection & column presentation** — which data-source fields are
   displayed, column order, column **width** and **format** per column.
3. **Grouping** — whether grouping is used; if grouping is defined the view
   also decides whether the grid **acts as a tree**.
4. **Row theme** — defines row height, color scheme, font, etc. for the
   entire grid.

Other decided points:

- **No page size, ever** — all grids use **infinite scrolling with cache
  optimization** for performance.
- **System views are read-only** — a user can open one, modify, and **save as
  a user-defined view** (never overwrite the system view).

**Design principle (Doug, verbatim intent):** give the user the ability to
define exactly the information they need for the tasks they perform
repeatedly — repeated tasks deserve an optimal UI.

## Data sources, themes, grouping (decided)

- **Authoring:** both a visual query builder AND raw SQL. Creation is
  restricted to **system administrators only** — sysadmins create the data
  sources that all users consume.
- **Security:** users are **granted permission per data source** — that is
  the approval boundary for what information a user can see. Data sources
  will frequently include a filter with **userID filtering** (e.g. restrict
  rows to the requesting user).
- **Conditional formatting** is part of the **theme** definition (not a
  separate view setting).
- **Grouping:** multiple nested levels, defined by the grouping in the data
  set.
- **Themes:** a **curated** set, optimized for different user preferences and
  functionality (not freely composable).

## View lifecycle & grouping details (decided)

- **User views are NOT sharable** user-to-user. **Admins can copy a user view
  and promote it to a system view** (the sharing path).
- Users can **update, rename, delete, and save** their own views.
- **A grid always opens on the view last displayed** on that list page
  (per user, persisted).
- **Default collapsed/expanded** for grouped/tree grids is a **view setting**.
- **Aggregates display on group rows AND at page level** (whole result set).

## Action bar — middle: search/filter box (decided)

- A **search/filter text box** sits in the middle of the action bar.
- **Searches on key press once 3+ characters** are typed.
- **Saves the last 5 searches** (recent-search history).
- Applied **on top of the view's own defined filters** — it further filters
  the view's results, never replaces them.
- Scope: **full text of all grid content**.

## Action bar — right: action buttons (decided)

- **Three buttons**, rightmost region of the action bar:
  1. + 2. The **two most common actions** for the data set, as direct buttons.
  3. **"Other Actions"** — a dropdown list box of **all actions defined for
     the data set**; the two common actions appear as its first two options,
     then all remaining actions.
- The **same full action list is available via right-click on the grid**
  (context menu).

## Actions — definition & invocation philosophy (decided)

- **Every data set ships standard CRUD actions**, plus any other **function or
  workprocess** defined for it. **Workprocesses = custom wizards defined by
  the system admin.**
- Actions **always act on the selected rows**.
- If an action cannot be applied to the current selection (no selection, not
  allowed for multiple selection, invalid selection), the system **displays a
  message explaining exactly why**.
- **Actions are NEVER hidden and NEVER disabled/grayed out.** Every action is
  always visible and invocable; an invalid invocation produces a **very
  detailed explanation** of why it cannot run in the current situation.
- **Design principle (Doug):** educate the user when they attempt an invalid
  action — discoverability and learning over prevention.

## Grid interaction: selection, clicks, sorting, column sizing (decided)

- **Single left-click** selects the row and deselects all others.
- **Shift/Ctrl-click** = standard multi-select; entering multi-select
  **displays a checkbox column** with the selected rows checked.
- **Double-click invokes the View action** (open the record).
- **Right-click opens the actions menu** (same list as "Other Actions").
- **Permissions:** all actions are permission-controlled, and all actions are
  **visible to every user who has access to the data set** (reduces
  training). To restrict functions for a group, **create a similar data set
  with tightened permissions** — visibility follows data-set access, never
  per-action hiding.
- **Multi-column sorting** is standard (header-driven), as is **column
  resize** by the user.
- **Smart resize:** on wide screens, columns automatically expand to show as
  much data as possible without manual resizing. **Rule: NO WHITE SPACE in a
  grid** (except when there's too little data to fill it even with everything
  displayed).
- The view defines **min/max column widths** (pixels or percentage), or
  **smart column sizing** that fits to the max width of ~90% of the data —
  ignoring unusually large outlier values.

## Status bar + aggregates placement (decided)

- **Aggregations live in a footer row inside the grid** (group rows carry
  group aggregates; the footer row carries page/result-set aggregates).
- The **status bar is reserved for grid statistics and messaging**:
  - **Right side:** row count of the filtered data set — e.g. **"200 rows"**;
    when more than one row is selected, the selected count appears next to
    it — **"200 rows, 10 Selected"**.
  - **Middle:** action progress/completion messages — e.g. "Loading grid",
    "Recalculating Averages".
  - **Next to the message: a progress bar** with an estimate, shown whenever
    an action runs longer than a few seconds.
  - Other/left space: reserved — situation-specific uses expected to emerge
    (per Doug, TBD as the app develops).

## Final grid clarifications (decided)

- **Double-click = the open/view-item action.**
- **Search history is grid-specific** (last 5 per grid).
- **Search scope = displayed columns only.**
- **Temporarily-applied view settings ARE indicated** in the view selector
  (modified marker).
- **The last action in every menu is Help** — provides **situation-specific
  help**. (Applies to every menu, app-wide.)

## Suggestions round (Claude proposed, Doug ruling one at a time)

1. **Server-side truth — IN.** Search, row counts, and all aggregates are
   computed server-side over the ENTIRE filtered result set; the
   infinite-scroll window affects only what's rendered, never what's counted,
   matched, or summed.

2. **Selection semantics — decided (Doug).**
   - **Select-all means select ALL** — the entire filtered result set, no
     two-step "visible first" pattern (explicitly rejected: "gmail sucks").
     Safety lives in the action, not the selection: invoking an action on a
     large selection warns the user it will run on "X items".
   - **Filter/search changes keep the selection, with notice** — status bar
     shows e.g. "10 selected, 3 not in current filter", and action
     confirmations spell out that hidden-selected rows are included.
     Silently deselecting a user-selected row is terrible practice.

3. **Keyboard model — IN (as defined).** Arrow up/down = row focus (visible
   indicator); Space = toggle selection; Shift+arrows = extend; Ctrl+A =
   select all (entire filtered set); Enter = open/view; Menu key / Shift+F10 =
   actions menu on selection; focus starts in the search box and `/` jumps to
   it; column-header focus + Enter sorts. Arrow-down at the bottom edge keeps
   loading (infinite scroll). One standard covers power users AND
   accessibility.

4. **Multi-sort mechanics — IN (as defined).** Click = sole sort (repeat
   toggles direction); Shift+click adds secondary/tertiary (repeat toggles,
   third removes); sorted headers show direction arrow + numbered badge for
   sort position. Header sorting is a temporary view modification — flags the
   view *(modified)*, lasts until another view is selected, saveable into a
   user view via the view editor.

5. **Grid state restoration — IN.** Returning from a record restores the grid
   exactly: view + temporary modifications, search text, scroll position,
   selection, focused row — but the DATA refreshes underneath (edited rows
   show new values; keep-with-notice applies if a selected row no longer
   matches). Lifetime: view choice persists long-term (already decided);
   scroll/selection/search restore is session-only.

6. **Export — IN; Print added (Doug).** Export is a standard action on every
   data set: exports the current view as seen (columns, order, formats, sort,
   filters + active search); selection if any, else entire filtered set
   (server-side); CSV + Excel (.xlsx); status-bar progress for long exports;
   **formatted values by default with an "export raw values" checkbox**.
   Grouping: flat with group columns, or Excel grouping — implementer's
   choice v1. **Doug: PRINT is also a standard grid function** — same scope
   semantics as export (selection else filtered set), print-friendly
   rendering of the current view; details at design time.

7. **Deep links — IN (as defined).** URL identifies grid + active view only
   (not search/sort/selection — session state per #5). Links are references:
   data-source permission still required; a link to another user's private
   view falls back to the recipient's last-used view with an educate-style
   notice; system-view links work for everyone with the data source.
   **Doug (preview of layout discussion): views will also be pinnable as
   tabs/menu items** — the system defines default tabs/menus, and users can
   add their own views to their tabs/menus for faster access (personal main
   navigation).

8. **Per-column (ad-hoc) filtering — IN, as a view setting.** The view
   decides whether ad-hoc header filters are allowed (funnel: distinct-value
   checkboxes / ranges / contains; filled funnel indicator; ANDs with view
   filters + search; marks view *(modified)*, saveable). **Doug's stated
   intent: well-designed saved views are the primary tool — ad-hoc filters
   are the escape hatch, not the culture.** Distinct-value lists come from
   the server-side filtered set.

9. **Empty/error states — IN (as defined).** Four distinguished states, each
   with the educate pattern (what happened → why → what next): view-empty
   (states the view's criteria), filtered-to-zero (says N rows hidden +
   [Clear search] — never let a filter masquerade as missing data),
   data-source error (plain words + [Retry], detail available not dumped),
   permission/no-access (which permission, who grants it). Standard defines
   the pattern; data set/view supplies wording.

10. **Destructive-action confirmation — IN (as defined).** Actions classified
    at definition: safe (no confirm) / modifying (confirm optional) /
    destructive (confirm required). Destructive confirms name the action,
    exact count, affected records (first N + "and X more"), and call out
    selected-but-filtered-out rows. One shared confirmation component
    app-wide. No type-to-confirm theatrics.
    **Doug (system-wide decision): the system uses SOFT DELETES — a record
    is never physically deleted.** Safety is assured at the data layer;
    confirmation wording must therefore be honest (not "cannot be undone" —
    deleted records are recoverable by admin).

11. **Inline cell editing — OUT for v1, revisit later.** If ever added it
    would likely be a view setting. Doug's expectation: the powerful action
    set + multi-select actions may make grid editing unnecessary.

**GRID STANDARD COMPLETE (2026-07-03).** All regions, behaviors, and edge
semantics ruled on by Doug. Next: rewrite the ui-standards skill grid section
from these notes for approval.

## Still open

- Overall layout (app shell, navigation, list/detail/edit relationships) —
  incl. Doug's pinned-views-as-tabs/menus concept (see #7) and grid-on-phone
  behavior.
- Expected UI functionality beyond grids (feedback patterns, unsaved changes,
  the situation-specific Help system, whether never-hide/never-disable is
  formally app-wide).
- Status bar left side: situation-specific uses TBD.
