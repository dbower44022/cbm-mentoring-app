/**
 * THE RENDERED-ANATOMY GATE (WTK-200 journeys + FND-909 conformance).
 *
 * Two jobs, one file, per the testing standard's spirit: (1) the smoke
 * journeys over the rendered shell — user journeys against the real backend
 * harness (tests/e2e_harness.py — the production app factory over a
 * migrated, SEEDED database with the CRM's HTTP edge faked), asserting the
 * served educate voice verbatim rather than re-mocking any of it; and
 * (2) CI-enforced assertions of the stakeholder-ruled UI ANATOMY — the grid
 * regions, the at-cursor menu, served formatting, chips, resize/selection,
 * splitters, session endings, and Home — so FND-909 ("all machine gates
 * green, rendered product garbage") can never recur silently: if the ruled
 * anatomy regresses, THIS suite is the gate that goes red.
 *
 * The file runs serially on one worker (playwright.config.ts): the journeys
 * share one seeded server whose read/acknowledgment receipts and engagement
 * statuses are per-user server state, so their order is part of the fixture
 * (the anatomy assertions read the seeded pending engagement's chip, so the
 * accept-assignment journey — which flips it for real — runs LAST).
 */

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = "http://127.0.0.1:8000";
// The seeded mentor (Frank Delgado). The harness CRM fake verifies the login
// NAME only — any password signs in — but the form still needs one typed.
const LOGIN = "frank";
const PASSWORD = "mentor-demo";

// FND-909 D2/D13: raw identifiers must never render — headers, cells, and
// message attributions all speak names. One pattern, used everywhere.
const UUID_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

// FND-909 D1: the raw SQL driver timestamp shape ("... 15:00:00.000000").
// Every cell renders through the ONE formatter, so this shape reaching the
// grid DOM means a value bypassed it.
const RAW_TIMESTAMP_PATTERN = /:\d{2}\.\d{6}/;

async function signIn(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Username").fill(LOGIN);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".shell-header")).toBeVisible();
}

async function seededState(
  request: APIRequestContext,
): Promise<{ summitEngagementID: string; riverbendEngagementID: string }> {
  const response = await request.get(`${API}/e2e/state`);
  const body = (await response.json()) as {
    data: { summitEngagementID: string; riverbendEngagementID: string };
  };
  return body.data;
}

/** The engagements grid with its rows loaded — the anatomy tests' subject. */
async function engagementsGrid(page: Page): Promise<ReturnType<Page["locator"]>> {
  const grid = page.locator("section.grid-panel");
  await expect(grid).toHaveAttribute("aria-label", "Engagements");
  await expect(grid.locator(".grid-table tbody tr").first()).toBeVisible();
  return grid;
}

test("login → engagements triage → docked rollup preview → prep → logout", async ({
  page,
}) => {
  await signIn(page);

  // REQ-072: the org-default startup lands the mentor on the Engagements
  // panel — "My Active Engagements", not Home.
  await expect(page).toHaveURL(/\/panel\/engagements/);
  const grid = page.locator("section.grid-panel");
  await expect(grid).toHaveAttribute("aria-label", "Engagements");

  // No banner interaction here: the boot pass mounts Home for one commit
  // before the startup navigation, and Home's render IS the read act
  // (REQ-011) — the seeded messages are read server-side by the time the
  // engagements grid shows. The banner journey below posts its own urgent
  // message and lands away from "/" to assert the banner mechanics.

  // The triage ruling (REQ-072/PI-010): pending acceptances lead, then the
  // engagement with the soonest next session.
  const rows = page.locator(".grid-table tbody tr");
  await expect(rows.first()).toContainText("Riverbend Bakery");
  await expect(rows.first()).toContainText("Pending Acceptance");
  await expect(rows.nth(1)).toContainText("Summit Auto Detail");

  // FND-909 D7 (REQ-045): the status value renders as a slot-colored chip,
  // never plain text. The computed colors prove the whole chain in a REAL
  // browser: served rule → statusWarning slot → --slot-status-warning
  // (#b45309) painting the chip's text AND border — visibly different from
  // the plain .status-chip default border (#c3ccd6), i.e. the slot really
  // drove the paint rather than the stylesheet fallback.
  const pendingChip = rows.first().locator(".status-chip.slot-colored");
  await expect(pendingChip).toHaveText("Pending Acceptance");
  await expect(pendingChip).toHaveCSS("color", "rgb(180, 83, 9)");
  await expect(pendingChip).toHaveCSS("border-top-color", "rgb(180, 83, 9)");

  // Selecting a row docks the engagement preview: the REQ-073/074 rollup
  // LEADS — notes + open action items across all sessions, newest first.
  await rows.nth(1).click();
  const preview = page.locator(".engagement-preview");
  await expect(preview).toContainText("Summit Auto Detail");
  await expect(preview).toContainText("Notes & open action items (all sessions)");
  await expect(preview).toContainText("Van lease vs. buy");
  await expect(preview).toContainText("Collect two commercial auto insurance quotes");

  // The session links open the /prep surface (WTK-177, REQ-081): the full
  // rollup plus the REQ-079 Join affordance for the scheduled session.
  await preview
    .getByRole("button", { name: /open prep surface/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/prep\//);
  const prep = page.locator(".prep-wrap");
  await expect(prep).toContainText(
    "All notes & action items across this engagement (newest first)",
  );
  await expect(prep.getByRole("link", { name: /Join Video Conference/ })).toBeVisible();
  await expect(prep).toContainText("Sessions held:");

  // Logout from the account menu is explicit and total: back to Sign in.
  await page.getByRole("button", { name: "Account ▾" }).click();
  await page.getByRole("menuitem", { name: /log out/i }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});

test("grid anatomy: one action bar, centered search, status bar, count footer", async ({
  page,
}) => {
  await signIn(page);
  const grid = await engagementsGrid(page);

  // REQ-016: the grid is three stacked regions, and region 1 is exactly ONE
  // action-bar row — view machinery, search, actions; never a second bar.
  const bar = grid.locator(".grid-action-bar");
  await expect(bar).toHaveCount(1);

  // The view selector is present and live (REQ-016/025).
  await expect(bar.getByRole("combobox", { name: "View" })).toBeVisible();

  // REQ-020/021 + the approved prototype: the search box sits in the bar's
  // CENTER region — the middle span takes the flexible width and centers it.
  // (Role is combobox, not searchbox: the input carries the recent-searches
  // datalist, which is REQ-020 anatomy in its own right.)
  const middle = bar.locator("> span").nth(1);
  await expect(
    middle.getByRole("combobox", { name: "Search displayed columns" }),
  ).toBeVisible();
  await expect(middle).toHaveCSS("flex-grow", "1");
  await expect(middle).toHaveCSS("justify-content", "center");

  // REQ-021: the right group is EXACTLY the data set's two common actions
  // plus "Other Actions" — no hidden actions, no extra buttons (FND-909 D6).
  // On the engagement source the two common actions are the leading
  // lifecycle transitions (mentoring/actions.ts).
  await expect(bar.locator("> span").nth(2).locator("button")).toHaveText([
    "Accept Assignment",
    "Decline Assignment",
    "Other Actions",
  ]);

  // Region 3 (D12/REQ-023/026): the bottom status bar counts the WHOLE
  // filtered set — the seeded 7 engagements — sitting at the RIGHT edge.
  const statusBar = grid.locator(".grid-status-bar");
  const counts = statusBar.locator("> span").last();
  await expect(counts).toHaveText("7 rows");
  const barBox = await statusBar.boundingBox();
  const countsBox = await counts.boundingBox();
  if (barBox === null || countsBox === null) {
    throw new Error("status bar did not render boxes");
  }
  expect(barBox.x + barBox.width - (countsBox.x + countsBox.width)).toBeLessThan(20);
  expect(countsBox.x - barBox.x).toBeGreaterThan(barBox.width / 2);

  // SKL-112 / FND-909 D11: the view's declared count aggregate renders as
  // the in-grid footer row (tfoot), agreeing with the status bar's count.
  await expect(grid.locator(".grid-table tfoot")).toBeVisible();
  await expect(grid.locator(".grid-table tfoot td").first()).toHaveText("7");
});

test("right-click opens the all-actions menu at the cursor; Help closes it", async ({
  page,
}) => {
  await signIn(page);
  const grid = await engagementsGrid(page);

  // REQ-106 (SKL-112 v3): the right-click menu opens AT the cursor — zero
  // mouse travel — so its top-left corner lands within 10px of the click
  // point (the clamp only engages near the window edges; this click isn't).
  const row = grid.locator(".grid-table tbody tr").nth(1);
  const rowBox = await row.boundingBox();
  if (rowBox === null) {
    throw new Error("row did not render a box");
  }
  const clickX = rowBox.x + 200;
  const clickY = rowBox.y + rowBox.height / 2;
  await page.mouse.click(clickX, clickY, { button: "right" });

  const menu = page.locator('menu[aria-label="All actions"]');
  await expect(menu).toBeVisible();
  const menuBox = await menu.boundingBox();
  if (menuBox === null) {
    throw new Error("menu did not render a box");
  }
  expect(Math.abs(menuBox.x - clickX)).toBeLessThanOrEqual(10);
  expect(Math.abs(menuBox.y - clickY)).toBeLessThanOrEqual(10);

  // REQ-043: every action menu's LAST item is Help — one resolver, never
  // hidden, always closing the list.
  await expect(menu.locator("li button").last()).toHaveText("Help");
});

test("served formatting: ruled headers, no raw values, type-driven alignment", async ({
  page,
}) => {
  await signIn(page);
  const grid = await engagementsGrid(page);

  // FND-909 D2/D11: the headers are the RULED wording — the engagement ID is
  // the row key, never a rendered column (no UUID column header), and the
  // session dates read "Last Session"/"Next Session", never the derived
  // "...At". Asserting the full list pins the anatomy exactly.
  await expect(grid.locator(".grid-table thead th")).toHaveText([
    "Engagement Name",
    "Engagement Status",
    "Primary Contact Name",
    "Primary Contact Email",
    "Last Session",
    "Next Session",
    "Total Sessions",
    "Open Action Items",
  ]);

  // FND-909 D1 (SKL-112): every cell renders through the one formatter, so
  // the raw SQL timestamp shape (":00.000000") and raw UUIDs must not exist
  // ANYWHERE in the grid DOM.
  const gridText = await grid.innerText();
  expect(gridText).not.toMatch(RAW_TIMESTAMP_PATTERN);
  expect(gridText).not.toMatch(UUID_PATTERN);

  // REQ-109 (SKL-112 v5): alignment rides the wire from the column's data
  // type — Doug's ruling: numbers CENTER, text/dates LEFT — and the grid
  // paints the served alignment on header and cells alike.
  const numberHeader = grid.locator('th[data-field="totalSessions"]');
  await expect(numberHeader).toHaveCSS("text-align", "center");
  const firstRow = grid.locator(".grid-table tbody tr").first();
  await expect(firstRow.locator("td").nth(6)).toHaveCSS("text-align", "center");
  const dateHeader = grid.locator('th[data-field="lastSessionAt"]');
  await expect(dateHeader).toHaveCSS("text-align", "left");
  await expect(firstRow.locator("td").nth(4)).toHaveCSS("text-align", "left");
});

test("column resize clamps at the character minimum; shift-click selects the row range", async ({
  page,
}) => {
  await signIn(page);
  const grid = await engagementsGrid(page);

  // REQ-107 (SKL-112 v4): every column is user-resizable by dragging its
  // header boundary. +150px grows the column by the drag delta…
  const th = grid.locator('th[data-field="engagementName"]');
  const startBox = await th.boundingBox();
  const handle = th.locator(".col-resize-handle");
  const handleBox = await handle.boundingBox();
  if (startBox === null || handleBox === null) {
    throw new Error("header did not render boxes");
  }
  // Grab INSIDE the handle's visible strip: the handle overhangs the header
  // boundary by 4px, and the overhang is clipped by the cell's
  // overflow:hidden — its box center is exactly on the clip edge.
  const grabX = handleBox.x + 2;
  const grabY = handleBox.y + handleBox.height / 2;
  await page.mouse.move(grabX, grabY);
  await page.mouse.down();
  await page.mouse.move(grabX + 150, grabY, { steps: 4 });
  await page.mouse.up();
  const grownBox = await th.boundingBox();
  if (grownBox === null) {
    throw new Error("header lost its box after the grow drag");
  }
  expect(grownBox.width).toBeGreaterThan(startBox.width + 100);
  expect(grownBox.width).toBeLessThan(startBox.width + 200);

  // Doug's 2026-07-07 clarification (REQ-107 / SKL-112 v6): a resize drag
  // must NEVER read as a header click — sorting stays exactly as it was.
  await expect(th).not.toHaveAttribute("aria-sort", /.+/);

  // …and a violent shrink drag clamps at the CHARACTER minimum ("Engagement
  // Name" floors at 17ch), never below 100px — the label always fits.
  const shrinkFrom = await handle.boundingBox();
  if (shrinkFrom === null) {
    throw new Error("resize handle lost its box");
  }
  const shrinkX = shrinkFrom.x + 2;
  const shrinkY = shrinkFrom.y + shrinkFrom.height / 2;
  await page.mouse.move(shrinkX, shrinkY);
  await page.mouse.down();
  await page.mouse.move(shrinkX - 900, shrinkY, { steps: 4 });
  await page.mouse.up();
  const clampedBox = await th.boundingBox();
  if (clampedBox === null) {
    throw new Error("header lost its box after the shrink drag");
  }
  expect(clampedBox.width).toBeGreaterThan(100);
  expect(clampedBox.width).toBeLessThan(startBox.width + 1);

  // REQ-023 (SKL-112 v4): shift-click extends the ROW selection — rows 0→4
  // select five — and the status bar counts them over the whole set.
  const rows = grid.locator(".grid-table tbody tr");
  await rows.first().click();
  await rows.nth(4).click({ modifiers: ["Shift"] });
  await expect(grid.locator('tbody input[type="checkbox"]:checked')).toHaveCount(5);
  await expect(grid.locator(".grid-status-bar > span").last()).toHaveText(
    "7 rows, 5 Selected",
  );
});

test("shift-click range leaves no native text selection (REQ-108)", async ({
  page,
}) => {
  // KNOWN PRODUCT DEFECT — expected failure, NOT a weakened assertion.
  // REQ-108's ruled selection hygiene (commit e37bb8b's own words: "native
  // text selection suppressed, 0 chars selected across the range") has no
  // implementing code: no user-select rule in shell.css and no shift-aware
  // mousedown preventDefault on the rows, so a shift-click row range
  // REQ-108 fixed in de342d1 (shift-aware mousedown preventDefault) — the
  // expected-failure marker that guarded this assertion is gone with it.
  await signIn(page);
  const grid = await engagementsGrid(page);
  const rows = grid.locator(".grid-table tbody tr");
  await rows.first().click();
  await rows.nth(4).click({ modifiers: ["Shift"] });
  await expect(grid.locator('tbody input[type="checkbox"]:checked')).toHaveCount(5);
  expect(await page.evaluate(() => window.getSelection()?.toString() ?? "")).toBe("");
});

test("panel splitters hold their dragged width; prep carries splitter + zoom", async ({
  page,
}) => {
  await signIn(page);
  await engagementsGrid(page);

  // REQ-087: the grid/preview boundary is a wide grabbable splitter, and the
  // dragged width HOLDS — the "splitter snaps back" regression (the saved
  // preference re-load clobbering the drag) stays dead. The preview is the
  // splitter's "next" sibling, so dragging LEFT grows it.
  const previewPanel = page.locator(".grid-preview-panel");
  const beforeBox = await previewPanel.boundingBox();
  const splitter = page.locator(".grid-with-preview > .panel-splitter");
  const splitterBox = await splitter.boundingBox();
  if (beforeBox === null || splitterBox === null) {
    throw new Error("preview/splitter did not render boxes");
  }
  const grabX = splitterBox.x + splitterBox.width / 2;
  const grabY = splitterBox.y + 200;
  await page.mouse.move(grabX, grabY);
  await page.mouse.down();
  await page.mouse.move(grabX - 120, grabY, { steps: 4 });
  await page.mouse.up();
  const draggedBox = await previewPanel.boundingBox();
  if (draggedBox === null) {
    throw new Error("preview lost its box after the drag");
  }
  expect(draggedBox.width).toBeGreaterThan(beforeBox.width + 80);
  // Outlive the 400ms debounced preference write and any refetch: the width
  // one second later is the width the drag left.
  await page.waitForTimeout(1100);
  const heldBox = await previewPanel.boundingBox();
  if (heldBox === null) {
    throw new Error("preview lost its box after the hold");
  }
  expect(Math.abs(heldBox.width - draggedBox.width)).toBeLessThanOrEqual(2);

  // The prep surface (REQ-087 over WTK-177, FND-909 D10): reached via the
  // preview's session link, it carries its own wide splitter and BOTH
  // panels' zoom controls (the main panel's and the entry side's).
  await page.locator(".grid-table tbody tr").nth(1).click();
  await page
    .locator(".engagement-preview")
    .getByRole("button", { name: /open prep surface/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/prep\//);
  await expect(page.locator(".prep-wrap > .panel-splitter")).toBeVisible();
  await expect(page.locator(".panel-zoom-control")).toHaveCount(2);
});

test("session expiry → in-place re-auth preserves the dirty input", async ({
  page,
  request,
}) => {
  await signIn(page);

  // Dirty a form the overlay must preserve: the quick-open palette's typed
  // query (the shell's one always-available text input).
  await page.keyboard.press("Control+k");
  const palette = page.getByRole("dialog", { name: "Quick open" });
  const paletteInput = palette.getByRole("textbox");
  await paletteInput.fill("Engagem");
  await expect(
    palette.getByRole("button", { name: /My Active Engagements/ }),
  ).toBeVisible();

  // Expire the session server-side, then let the next keystroke's query hit
  // the refusal: the envelope client holds it and the overlay appears IN
  // PLACE — no navigation, the palette stays mounted behind it.
  await request.post(`${API}/e2e/session/expire`);
  await paletteInput.fill("Engagement");
  const overlay = page.locator(".auth-overlay");
  await expect(overlay.getByText("Your session has expired.")).toBeVisible();
  await expect(paletteInput).toHaveValue("Engagement");

  // One re-login revives the session; the held request replays and the
  // dirty input is exactly as typed.
  await overlay.getByLabel("Password").fill(PASSWORD);
  await overlay.getByRole("button", { name: "Sign back in" }).click();
  await expect(overlay).toHaveCount(0);
  await expect(paletteInput).toHaveValue("Engagement");
  await expect(
    palette.getByRole("button", { name: /My Active Engagements/ }),
  ).toBeVisible();
});

test("a corrupted stored session lands on the ended screen, never an internal error", async ({
  page,
}) => {
  await signIn(page);

  // FND-909 D9: corrupt the persisted session REFERENCE (api/session.ts
  // stores the whole session under "mentorapp.session"; the reference is the
  // only identity the client ever sends). The next boot's reads all answer
  // `unauthenticated` — beyond revival — and the app must land on the
  // session-ended overlay or the sign-in screen, NEVER a raw internal error.
  await page.evaluate(() => {
    const raw = localStorage.getItem("mentorapp.session");
    const stored = raw === null ? {} : (JSON.parse(raw) as Record<string, unknown>);
    localStorage.setItem(
      "mentorapp.session",
      JSON.stringify({ ...stored, sessionReference: "corrupted-beyond-revival" }),
    );
  });
  await page.reload();

  const overlay = page.locator(".auth-overlay");
  await expect(overlay.getByText("This session has ended.")).toBeVisible();
  const bodyText = await page.locator("body").innerText();
  expect(bodyText).not.toMatch(/internal (server )?error/i);

  // Leaving the ended session is explicit; the same credentials sign back in.
  await overlay.getByRole("button", { name: "Sign in again" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Username").fill(LOGIN);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".shell-header")).toBeVisible();
});

test("urgent banner and admin-message acknowledgment", async ({ page, request }) => {
  // Seed by the app's own admin surface (the real StoredMessageCenter): one
  // urgent message requiring acknowledgment and one normal one (Home only)
  // that also requires it. Since FND-909 D9 the server resolves the acting
  // user from the session reference, so the poster is a REAL login (janet,
  // the seeded Leadership account) — no claimed identity header exists.
  const admin = { "X-Session-Reference": await sessionReference(request, "janet") };
  await request.post(`${API}/home/messages`, {
    headers: admin,
    data: {
      title: "Maintenance tonight",
      body: "The CRM pauses at 22:00 for maintenance.",
      priority: "urgent",
      requiresAcknowledgment: true,
    },
  });
  await request.post(`${API}/home/messages`, {
    headers: admin,
    data: {
      title: "New mentoring guide",
      body: "The updated mentoring guide is on the wiki.",
      priority: "normal",
      requiresAcknowledgment: true,
    },
  });

  // Land on a NON-Home panel: Home's render is itself the read act
  // (REQ-011), which would clear the banner before it could be asserted.
  // Sign in through the API and inject the stored session so Home never
  // mounts on the way in.
  const login = await request.post(`${API}/auth/login`, {
    data: { loginName: LOGIN, password: PASSWORD },
  });
  const session = (
    (await login.json()) as {
      data: { sessionReference: string; userID: string; roleNames: string[] };
    }
  ).data;
  await page.addInitScript(
    (stored) => {
      localStorage.setItem("mentorapp.session", JSON.stringify(stored));
    },
    { ...session, loginName: LOGIN },
  );
  await page.goto("/panel/resources");
  await expect(page.locator("section.grid-panel")).toHaveAttribute(
    "aria-label",
    "Resources",
  );
  // The seeded staff-maintained library serves read-only (REQ-084).
  await expect(page.locator(".grid-table")).toContainText(
    "One-Page Business Plan Template",
  );

  // The urgent banner banners across panels until read; opening it is the
  // read, and the acknowledgment is its own explicit click.
  const banner = page.getByRole("alert", { name: "Urgent messages" });
  await expect(banner).toContainText(
    "Urgent message from your administrator: Maintenance tonight",
  );
  await banner.getByRole("button", { name: "Read message" }).click();
  await expect(banner).toContainText("The CRM pauses at 22:00 for maintenance.");
  await banner.getByRole("button", { name: "Acknowledge" }).click();
  await expect(banner.getByText("Acknowledged.")).toBeVisible();
  // Dismiss removes the expanded message; the banner region itself only
  // clears on the next fetch of read state (Home's view triggers one).
  await banner.getByRole("button", { name: "Dismiss" }).click();
  await expect(
    banner.getByText("The CRM pauses at 22:00 for maintenance."),
  ).toHaveCount(0);

  // Home's dashlet shows the normal message with its own acknowledgment.
  // Reached IN-APP via the quick-open palette: a fresh page load of "/"
  // would re-run the startup redirect (REQ-072 lands mentors back on
  // Engagements), while client-side navigation honors it once per boot.
  await page.keyboard.press("Control+k");
  await page
    .getByRole("dialog", { name: "Quick open" })
    .getByRole("button", { name: "panel Home" })
    .click();
  const normal = page.getByRole("article", { name: "New mentoring guide" });
  await expect(normal).toContainText("The updated mentoring guide is on the wiki.");
  await normal.getByRole("button", { name: "Acknowledge" }).click();
  await expect(normal.getByText("Acknowledged.")).toBeVisible();
});

test("Home leads the navigation; its messages dashlet attributes posters by name", async ({
  page,
}) => {
  await signIn(page);

  // FND-909 D8 (REQ-011/REQ-071): Home is a served AREA anchoring the
  // navigation — the FIRST item, on every presentation.
  const nav = page.getByRole("navigation", { name: "Navigation" });
  await expect(nav.locator("button.nav-item").first()).toHaveText("Home");

  // Clicking Home renders the provided, always-first messages dashlet
  // (REQ-003/REQ-011) — client-side navigation, since the once-per-boot
  // startup claim already happened (D8).
  await nav.locator("button.nav-item").first().click();
  const home = page.locator("main.home-panel");
  await expect(home).toBeVisible();
  const dashlet = home.locator("section.dashlet").first();
  await expect(dashlet).toHaveAttribute(
    "aria-label",
    "Messages from your administrator",
  );

  // FND-909 D13: "Posted by" is followed by a NAME — the server resolves the
  // poster's stored userID to a login name; a raw UUID rendering here is the
  // D2 defect recurring on messages.
  const postedLine = dashlet
    .locator("article p")
    .filter({ hasText: "Posted by" })
    .first();
  await expect(postedLine).toBeVisible();
  const line = await postedLine.innerText();
  const match = /^Posted by (.+?) on /.exec(line);
  expect(match).not.toBeNull();
  expect(match?.[1]).not.toMatch(UUID_PATTERN);
});

test("broken pin explains itself and dismisses without change", async ({ page }) => {
  await signIn(page);

  // The seeded pin whose view no longer exists is visible, marked, and
  // still clickable — never hidden, never disabled; activation answers the
  // educate dialog (REQ-015).
  const brokenPin = page.getByRole("button", { name: /Q2 Pipeline Review/ });
  await expect(brokenPin).toContainText("⚠");
  await brokenPin.click();

  const dialog = page.getByRole("dialog", { name: "This pin needs attention" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".educate-what")).not.toBeEmpty();
  await expect(dialog.locator(".educate-why")).not.toBeEmpty();
  await expect(dialog.locator(".educate-next")).not.toBeEmpty();
  await expect(dialog.getByRole("button", { name: "Remove this pin" })).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Choose a different view" }),
  ).toBeVisible();

  // Dismissing changes nothing: the pin is still there, still marked.
  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(brokenPin).toBeVisible();

  // The healthy pin resolves and opens its panel — the seeded REQ-072 view.
  await page.getByRole("button", { name: "My Active Engagements" }).click();
  await expect(page).toHaveURL(/\/panel\/engagements/);
});

test("a CRM outage at login never reads as a wrong password", async ({
  page,
  request,
}) => {
  await request.post(`${API}/e2e/crm/outage`, { data: { down: true } });
  try {
    await page.goto("/");
    await page.getByLabel("Username").fill(LOGIN);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();

    // The served outage message, verbatim — and positively NOT the
    // credentials-rejected wording (the distinctness the standard demands).
    const notice = page.getByRole("alert");
    await expect(notice).toContainText("Sign-in couldn't be checked.");
    await expect(notice).toContainText("isn't reachable right now");
    await expect(notice).not.toContainText("wasn't accepted");
  } finally {
    await request.post(`${API}/e2e/crm/outage`, { data: { down: false } });
  }

  // With the CRM back, the same credentials sign in.
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".shell-header")).toBeVisible();
});

// --- REL-004 block 1: forms & editing (REQ-032..040) ---------------------------------

test("Edit action → full-screen form: read-only explains, help reveals, Ctrl+S saves, Escape guards", async ({
  page,
}) => {
  await signIn(page);
  const rows = page.locator(".grid-table tbody tr");
  await rows.nth(1).click(); // Summit Auto Detail

  // The Edit action joins the one full menu (REQ-032) and opens the
  // record's PINNED pop-out window at the edit route — a real window.
  await page.getByRole("button", { name: "Other Actions" }).click();
  const popupPromise = page.waitForEvent("popup");
  await page
    .locator('menu[aria-label="All actions"]')
    .getByRole("button", { name: "Edit", exact: true })
    .click();
  const editor = await popupPromise;
  await expect(editor).toHaveURL(/\/records\/engagement\/[0-9a-f-]+\/edit$/);

  // REQ-032 anatomy: the full-screen form with the LARGE Save.
  const form = editor.locator("form.edit-form");
  await expect(form).toBeVisible();
  const save = form.getByRole("button", { name: "Save" });
  await expect(save).toHaveAttribute("data-prominence", "large");

  // REQ-039: the structural field renders read-only IN PLACE — clicking it
  // explains in the educate voice instead of opening an editor.
  await form.locator('[data-readonly-kind="system"]').first().click();
  await expect(
    editor.getByRole("dialog", { name: "Why this field is not editable" }),
  ).toContainText("This is a system field; it is maintained automatically.");
  await editor.getByRole("button", { name: "Got it" }).click();

  // REQ-040: the admin-maintained help text reveals from the label marker —
  // a subtle affordance, not a permanent paragraph.
  const summaryField = form
    .locator(".form-field")
    .filter({ has: editor.getByText("Engagement Summary") })
    .first();
  await expect(summaryField.locator(".field-help-text")).toBeHidden();
  await summaryField.locator(".field-help-marker").hover();
  await expect(summaryField.locator(".field-help-text")).toContainText("at a glance");

  // REQ-036: a reference field renders the ONE lookup control — a combobox
  // over the durable lookupSourceBinding store (PI-012), with its two
  // always-visible inline affordances. The whole gate running with NO
  // resolver stub (harness uses the real StoredLookupSources over the seeded
  // bindings) is what proves the durable chain end to end; this pins the
  // rendered control so a regression to a bare input goes red.
  const clientField = form
    .locator(".form-field")
    .filter({ has: editor.getByRole("combobox", { name: "Client ID" }) })
    .first();
  await expect(clientField.getByRole("button", { name: "Open" })).toBeVisible();
  await expect(clientField.getByRole("button", { name: "New…" })).toBeVisible();

  // Edit a field, then Escape: the dirty guard NAMES the changed field and
  // navigation does not proceed (REQ-032/038) — real keyboard input.
  const contact = form.getByLabel("Primary Contact Name");
  await contact.fill("Deshawn Carter Jr.");
  await contact.press("Escape");
  const guard = editor.getByRole("alertdialog", { name: "Unsaved changes" });
  await expect(guard).toContainText("Primary Contact Name");
  await guard.getByRole("button", { name: "Keep editing" }).click();

  // Enter never submits a multi-field form (REQ-038): no save confirmation.
  await contact.press("Enter");
  await expect(editor.locator(".save-confirmation")).toHaveCount(0);

  // Ctrl+S saves (REQ-038): the PATCH lands, the form stays open and rebases.
  await contact.press("Control+s");
  await expect(editor.locator(".save-confirmation")).toContainText("Saved at");

  // Cancel now reverts relative to the SAVED originals: no guard on leave.
  await editor.getByRole("button", { name: "Close" }).click();
  await expect(editor).toHaveURL(/\/records\/engagement\/[0-9a-f-]+$/);
  await expect(editor.locator("dl")).toContainText("Deshawn Carter Jr.");
  await editor.close();
});

test("double-click on the preview opens the per-field window; one field commits alone", async ({
  page,
  request,
}) => {
  const state = await seededState(request);
  await signIn(page);
  await page.goto(`/records/engagement/${state.summitEngagementID}`);
  await expect(page.locator("dl")).toBeVisible();

  // REQ-035: double-click the field's read-only element → the SMALL
  // self-contained window, focus in its one editor.
  const contactRow = page
    .locator("dl > div")
    .filter({ has: page.getByText("primaryContactName", { exact: true }) });
  await contactRow.dblclick();
  const window = page.locator('[data-kind="smallWindow"]');
  await expect(window).toBeVisible();
  await expect(window).toHaveAttribute("data-commits", "singleFieldPatch");

  const editor = window.getByRole("textbox");
  await editor.fill("Maria Kovac-Ellis");
  await window.getByRole("button", { name: "Save" }).click();
  // The single-field write landed and the preview re-read its content.
  await expect(page.locator("dl")).toContainText("Maria Kovac-Ellis");
  await expect(window).toHaveCount(0);

  // A structural field explains instead of opening (REQ-039's words, both
  // gestures — never a silent nothing).
  const createdRow = page
    .locator("dl > div")
    .filter({ has: page.getByText("createdAt", { exact: true }) });
  await createdRow.dblclick();
  await expect(
    page.getByRole("dialog", { name: "Why this field is not editable" }),
  ).toContainText("This is a system field; it is maintained automatically.");

  // Esc IS Cancel in this window: a second double-click, edit, then Escape
  // discards directly — no guard, nothing written.
  await page.getByRole("button", { name: "Got it" }).click();
  await contactRow.dblclick();
  await window.getByRole("textbox").fill("Someone Else");
  await window.getByRole("textbox").press("Escape");
  await expect(window).toHaveCount(0);
  await expect(page.locator("dl")).toContainText("Maria Kovac-Ellis");
});

test("New → similar-records offer compares without blocking; continue records the override", async ({
  page,
}) => {
  await signIn(page);
  // The resources library: its create needs only the fields a journey can
  // type (title + location), so the whole REQ-037 flow renders end to end.
  await page.goto("/panel/resources");
  const grid = page.locator("section.grid-panel");
  await expect(grid).toHaveAttribute("aria-label", "Resources");
  await page.getByRole("button", { name: "Other Actions" }).click();
  const popupPromise = page.waitForEvent("popup");
  await page
    .locator('menu[aria-label="All actions"]')
    .getByRole("button", { name: "New", exact: true })
    .click();
  const creator = await popupPromise;
  await expect(creator).toHaveURL(/\/records\/resource\/new$/);
  const form = creator.locator("form.create-form");
  await expect(form).toBeVisible();

  // Typing an existing resource's title arms the ADVISORY check on exit:
  // the offer compares side by side and is declared non-blocking (REQ-037).
  const title = form.getByLabel("Resource Title");
  await title.fill("Pricing & Margin Worksheet");
  await title.press("Tab");
  const offer = creator.getByRole("dialog", { name: "Similar records exist" });
  await expect(offer).toBeVisible();
  await expect(offer).toHaveAttribute("data-blocking", "false");
  await expect(offer.locator(".offer-comparison")).toContainText(
    "Pricing & Margin Worksheet",
  );
  // Continue just dismisses — Save remains the user's act.
  await offer.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(offer).toHaveCount(0);

  // The save sweep still guards required settings (REQ-033): the other
  // required field settings get real values before Save.
  await form
    .getByLabel("Resource Location")
    .fill("https://library.cbm.example/pricing-v2");
  await form.getByLabel("Resource Kind").selectOption({ label: "Document" });

  // Save → the server's DB-S12 duplicate rejection re-presents the offer
  // ENFORCED; continuing resubmits with the recorded override (REQ-059).
  await form.getByRole("button", { name: "Save" }).click();
  await expect(offer).toBeVisible();
  await offer
    .getByLabel(/Why is this not a duplicate/)
    .fill("updated worksheet, keeping both");
  await offer.getByRole("button", { name: "Continue and create anyway" }).click();

  // The first save lands on the NEW record's read view (REQ-037).
  await expect(creator).toHaveURL(/\/records\/resource\/[0-9a-f-]+$/);
  await expect(creator.locator("dl")).toContainText("Pricing & Margin Worksheet");
  await creator.close();
});

test("accept assignment: pending engagement flips with next steps offered", async ({
  page,
  request,
}) => {
  const state = await seededState(request);
  await signIn(page);

  // The pending engagement leads the triage grid (journey order matters:
  // this runs LAST because the flip is real server state).
  const rows = page.locator(".grid-table tbody tr");
  await expect(rows.first()).toContainText("Riverbend Bakery");
  await rows.first().click();

  // Every mentoring action is always listed (never hidden); the lifecycle
  // transitions live in the actions menu and confirm before anything moves.
  // FND-910: "Accept Assignment" legitimately exists TWICE — as a common
  // action-bar button (REQ-021's two-most-common rule) and as an entry in
  // the one full menu — so the click scopes to the container it means: the
  // bar is the toolbar (.grid-action-bar), the open menu is
  // menu[aria-label="All actions"]. Product behavior is correct; an
  // unscoped page-wide selector was the defect.
  await page
    .locator(".grid-action-bar")
    .getByRole("button", { name: "Other Actions" })
    .click();
  await page
    .locator('menu[aria-label="All actions"]')
    .getByRole("button", { name: "Accept Assignment" })
    .click();
  const dialog = page.getByRole("dialog", { name: "Accept Assignment" });
  await expect(dialog).toContainText("Riverbend Bakery");
  await expect(dialog).toContainText("Pending Acceptance");
  await dialog.getByRole("button", { name: "Continue" }).click();

  // The server confirmation names the flip and offers the REQ-076 first
  // steps in place: the intro email and the first session.
  await expect(dialog).toContainText("accepted — its status is now Assigned");
  await expect(
    dialog.getByRole("button", { name: "Send the introduction email" }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Schedule the first session" }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Close" }).click();

  // The grid refreshed in place: nothing is Pending Acceptance any more,
  // and the rollup read agrees the status is Assigned.
  await expect(page.locator(".grid-table")).not.toContainText("Pending Acceptance");
  const rollup = await request.get(
    `${API}/engagements/${state.riverbendEngagementID}/rollup`,
    { headers: { "X-Session-Reference": await sessionReference(request, LOGIN) } },
  );
  const body = (await rollup.json()) as {
    data: { engagement: { engagementStatusLabel: string } };
  };
  expect(body.data.engagement.engagementStatusLabel).toBe("Assigned");
});

/** A real session for API-side asserts: identity is server-resolved (D9). */
async function sessionReference(
  request: APIRequestContext,
  loginName: string,
): Promise<string> {
  const login = await request.post(`${API}/auth/login`, {
    data: { loginName, password: PASSWORD },
  });
  return ((await login.json()) as { data: { sessionReference: string } }).data
    .sessionReference;
}
