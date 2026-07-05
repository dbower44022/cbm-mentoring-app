# The layout & window standard

The app shell, navigation, and multi-window model. Approved by Doug
2026-07-03 (source: `docs/ui-standards-discussion.md`).

## Panels — the unifying concept

- The UI is composed of **panels**: **grid** (most common), **dashboard**,
  and future types (**Gantt**, **graph/chart** — not yet specified).
- The general UI = navigation among the panels **available to the user**
  (permissioned via their data sources).
- A grid panel = a grid on a data set (full action bar inside). A pinned
  tab/menu item = **panel + view reference** ("Engagements → Needs
  follow-up").
- Target platform: **desktop and web first**. Phones come later as a
  **separate app** — do not compromise this UI for small screens.

## Navigation

- **Per-user presentation preference, switchable anytime:** tabs across the
  top (few panels) / vertical side menu (more) / **panel-group tree** on the
  left (many). The pin set is the data; presentation is just rendering.
- System-defined default tabs/menus + **user-pinned views** for fast access.
- **Ctrl+K quick-open palette:** type-ahead search over every panel and view
  the user can access.
- **Broken pins are never silently removed:** a pin whose view/permission
  went away stays visible (subtly marked); clicking explains exactly what
  happened (what, by whom, when) with [Remove this pin] / [Choose a
  different view]. Deep links and startup use the same fallback — "Open to
  last Panel" pointing at something inaccessible lands on Home with the
  explanation, never a blank screen.

## Home panel & startup

- **Home** = sysadmin messaging + the user's chosen dashlets.
- Startup preference per user: **Open to last Panel** or **Open to Default**
  (system default: Home).
- **Messaging mechanics:** message-list dashlet (title/body/posted-by/date,
  newest first; admin-set expiration); per-user read/unread + unread count;
  auto-read on view; optional **"requires acknowledgment"** flag (explicit
  click; admin sees who hasn't); priority **normal** (Home only) vs
  **urgent** (banner across every panel until read).

## Dashboards & dashlets

A dashlet is **any view or panel rendered small** — a view carries a
property listing it as available as a dashlet. System- and user-defined
dashlets follow the same model (and permissions) as views. Users choose and
arrange their own dashlets on Home/dashboards.

## Preview & record windows

- Selecting a row always feeds a **read-optimized preview**: no edit
  controls, optimized for readability. **Docked** when window size allows
  (dock position: implementer's choice at design time); **live-updates
  immediately** to the selected row.
- **Two paths into editing:** the Edit action swaps the preview for a
  typical edit screen; **double-click any read-only element opens a
  per-field edit window** (edit just that field).
- **Pop-outs are real browser windows** (multi-monitor). A pop-out is
  **pinned to its record**; **multiple pop-outs** may be open at once; the
  docked preview keeps following the selection.
- Multiple **live panels** may be open simultaneously across windows and
  monitors.

## Cross-window behavior

- **Same-user sync is standard (v1):** any save in any window updates every
  open window showing that record or a grid containing it
  (BroadcastChannel-class mechanism).
- **Multi-user liveness (server push) is designed-for but delivered later**
  — the architecture must not preclude it; Refresh covers the gap.
- **Multi-window editing rules:** every window with unsaved changes guards
  its close (the main window warns when pop-outs hold unsaved edits: "2 open
  windows have unsaved changes"). Invoking Edit on a record already open for
  editing in another of the user's windows offers **[Switch to that
  window]** instead of opening a second editor — record-level for full edit
  screens, field-level for per-field editors.
- **Optimistic concurrency is standard:** every save verifies the record
  hasn't changed since load; a conflict gets educate-voice resolution (show
  what changed and who changed it; never silently overwrite either side).

## Session lifecycle

One login session spans all windows. On expiry, every window shows re-auth
**in place**; unsaved work survives; one re-login re-authenticates all
windows. Pop-outs survive main-window close. Logout is explicit and total
across windows (dirty-window guards run first). **Workspace restore**
(reopen last session's panels, placement best-effort) is a user preference —
**deferred to v1.5**.

## Background tasks & notifications

- **Anything that can run longer than ~10 seconds MUST be a background
  task** — never a spinner the user can't walk away from. The originating
  panel stays free; its status bar shows progress while the user is there.
- A **notification bell (with count) in the header** collects completions
  and failures ("Export ready [Download]"; failures in educate voice).
  Per-user, read-on-view, expiring. Future multi-user push lands in this
  same channel.

## Standard header

One thin header on every window (data density starts below it): app
identity + the user's navigation on the left; **notification bell, Help,
user menu** on the right. The user menu is the address for all per-user
preferences: navigation style, startup, themes, manage views/pins, logout.
Pop-outs get the header **minus navigation** (they're record windows, not
panel hosts).

## Density & scaling principle

All panels and dialogs **scale to full screen** and show as much data as
possible. **"White space is a waste"** — always data-dense, never
junky/crowded. (The grid NO-WHITE-SPACE rule, applied app-wide.)

## Panel resizing & per-panel zoom (REQ-087, ruled by Doug 2026-07-05 at the prototype gate)

- **All panels are resizable.** Every boundary between panels is a very
  clear, wider-than-normal border, easy to select and drag to resize.
- **Panel dimensions persist:** the system remembers the user's last panel
  dimensions and defaults to them when the app/page is next opened
  (long-term per-user persistence, like view choice — not session-only).
- **Per-panel zoom:** each panel supports a user-defined zoom level, set by
  the user and remembered (same persistence class).
