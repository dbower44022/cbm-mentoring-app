/**
 * The WTK-200 smoke journeys over the rendered shell, in the testing
 * standard's spirit: user journeys against the real backend harness
 * (tests/e2e_harness.py — seeded data, stubbed CRM), asserting the served
 * educate voice verbatim rather than re-mocking any of it. The file runs
 * serially on one worker (playwright.config.ts): the journeys share one
 * seeded server whose read/acknowledgment receipts are per-user state, so
 * their order is part of the fixture (messages seed in the banner journey
 * and are read by its end).
 */

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = "http://127.0.0.1:8000";
const LOGIN = "mentor@cbm.org";
const PASSWORD = "correct-horse";

async function signIn(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Username").fill(LOGIN);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".shell-header")).toBeVisible();
}

async function seededRecordId(request: APIRequestContext): Promise<string> {
  const response = await request.get(`${API}/e2e/state`);
  const body = (await response.json()) as { data: { recordId: string } };
  return body.data.recordId;
}

test("login → home → navigate → preview → pop-out → logout", async ({
  page,
  request,
}) => {
  await signIn(page);

  // Home: the messages dashlet is always first; nothing is seeded yet.
  await expect(
    page.getByText("No messages from your administrator right now."),
  ).toBeVisible();

  // Navigate: the healthy pin opens its panel (placeholder content today —
  // grids land with PI-003; the navigation itself is what this asserts).
  await page.getByRole("button", { name: "Active Mentors" }).click();
  await expect(page.getByText("Panel “mentors” is open")).toBeVisible();

  // Preview: the record window route renders the read-optimized preview.
  const recordId = await seededRecordId(request);
  await page.goto(`/records/mentor/${recordId}`);
  const preview = page.getByRole("article", { name: "Record preview" });
  await expect(preview).toBeVisible();
  await expect(preview).toContainText("Ada Lovelace");

  // Pop-out: a REAL browser window named for its record (windows/record.tsx
  // popOutRecord semantics), sharing the localStorage session.
  const popupPromise = page.waitForEvent("popup");
  await page.evaluate((id) => {
    window.open(`/records/mentor/${id}`, `record:mentor:${id}`, "popup=yes");
  }, recordId);
  const popup = await popupPromise;
  await expect(popup.getByRole("article", { name: "Record preview" })).toContainText(
    "Ada Lovelace",
  );
  await popup.close();

  // Logout from the account menu is explicit and total: back to Sign in.
  await page.goto("/");
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
  // query (the shell's one text input until edit screens land with PI-003).
  await page.keyboard.press("Control+k");
  const palette = page.getByRole("dialog", { name: "Quick open" });
  const paletteInput = palette.getByRole("textbox");
  await paletteInput.fill("Active");
  await expect(palette.getByRole("button", { name: /Active Mentors/ })).toBeVisible();

  // Expire the session server-side, then let the next keystroke's query hit
  // the refusal: the envelope client holds it and the overlay appears IN
  // PLACE — no navigation, the palette stays mounted behind it.
  await request.post(`${API}/e2e/session/expire`);
  await paletteInput.fill("Active M");
  const overlay = page.locator(".auth-overlay");
  await expect(overlay.getByText("Your session has expired.")).toBeVisible();
  await expect(paletteInput).toHaveValue("Active M");

  // One re-login revives the session; the held request replays and the
  // dirty input is exactly as typed.
  await overlay.getByLabel("Password").fill(PASSWORD);
  await overlay.getByRole("button", { name: "Sign back in" }).click();
  await expect(overlay).toHaveCount(0);
  await expect(paletteInput).toHaveValue("Active M");
  await expect(palette.getByRole("button", { name: /Active Mentors/ })).toBeVisible();
});

test("urgent banner and admin-message acknowledgment", async ({ page, request }) => {
  // Seed by the app's own admin surface: one urgent message requiring
  // acknowledgment and one normal one (Home only) that also requires it.
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
  await signIn(page);
  await page.getByRole("button", { name: "Active Mentors" }).click();
  await expect(page.getByText("Panel “mentors” is open")).toBeVisible();
  await page.reload();

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
  await banner.getByRole("button", { name: "Dismiss" }).click();
  await expect(banner).toHaveCount(0);

  // Home's dashlet shows the normal message with its own acknowledgment.
  await page.goto("/");
  const normal = page.getByRole("article", { name: "New mentoring guide" });
  await expect(normal).toContainText("The updated mentoring guide is on the wiki.");
  await normal.getByRole("button", { name: "Acknowledge" }).click();
  await expect(normal.getByText("Acknowledged.")).toBeVisible();
});

test("broken pin explains itself and dismisses without change", async ({ page }) => {
  await signIn(page);

  // The tombstoned view's pin is visible, marked, and still clickable —
  // never hidden, never disabled; activation answers the educate dialog.
  const brokenPin = page.getByRole("button", { name: /Retired Mentors/ });
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
