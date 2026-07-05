/**
 * Playwright per the testing standard (WTK-200): the smoke journeys run
 * against the REAL stack — the seeded, stub-CRM FastAPI harness
 * (tests/e2e_harness.py) behind the same Vite proxy the app uses in dev —
 * never against mocked fetches. One worker: the journeys share one seeded
 * server and its read/acknowledgment receipts are per-user server state.
 */

import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 30_000,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: "http://127.0.0.1:5173",
  },
  webServer: [
    {
      command: "uv run uvicorn tests.e2e_harness:app --host 127.0.0.1 --port 8000",
      cwd: "..",
      url: "http://127.0.0.1:8000/healthz",
      reuseExistingServer: !process.env.CI,
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: !process.env.CI,
    },
  ],
});
