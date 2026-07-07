/**
 * The WTK-200 smoke journeys over the rendered shell, in the testing
 * standard's spirit: user journeys against the real backend harness
 * (tests/e2e_harness.py — the production app factory over a migrated,
 * SEEDED database with the CRM's HTTP edge faked), asserting the served
 * educate voice verbatim rather than re-mocking any of it. The file runs
 * serially on one worker (playwright.config.ts): the journeys share one
 * seeded server whose read/acknowledgment receipts and engagement statuses
 * are per-user server state, so their order is part of the fixture (the
 * first journey reads the seeded urgent banner; the last journey accepts
 * the seeded pending engagement).
 */

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = "http://127.0.0.1:8000";
// The seeded mentor (Frank Delgado). The harness CRM fake verifies the login
// NAME only — any password signs in — but the form still needs one typed.
const LOGIN = "frank";
const PASSWORD = "mentor-demo";

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
  // never plain text. The computed color proves the whole chain in a REAL
  // browser: served rule → statusWarning slot → --slot-status-warning
  // (#b45309) painting the chip text.
  const pendingChip = rows.first().locator(".status-chip.slot-colored");
  await expect(pendingChip).toHaveText("Pending Acceptance");
  await expect(pendingChip).toHaveCSS("color", "rgb(180, 83, 9)");

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

test("urgent banner and admin-message acknowledgment", async ({ page, request }) => {
  // Seed by the app's own admin surface (the real StoredMessageCenter): one
  // urgent message requiring acknowledgment and one normal one (Home only)
  // that also requires it.
  const admin = { "X-User-ID": "018f0000-0000-7000-8000-000000000abc" };
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
  await page.getByRole("button", { name: "Other Actions" }).click();
  await page.getByRole("button", { name: "Accept Assignment" }).click();
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
    { headers: { "X-User-ID": await frankUserId(request) } },
  );
  const body = (await rollup.json()) as {
    data: { engagement: { engagementStatusLabel: string } };
  };
  expect(body.data.engagement.engagementStatusLabel).toBe("Assigned");
});

async function frankUserId(request: APIRequestContext): Promise<string> {
  const login = await request.post(`${API}/auth/login`, {
    data: { loginName: LOGIN, password: PASSWORD },
  });
  return ((await login.json()) as { data: { userID: string } }).data.userID;
}
