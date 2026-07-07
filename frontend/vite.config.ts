import react from "@vitejs/plugin-react";
// vitest/config's defineConfig is vite's plus the `test` block — the one
// config file serves both tools.
import { defineConfig } from "vitest/config";

// Every API prefix the FastAPI app mounts (mentorapp.main.create_app). The dev
// server proxies them so the browser and the API share one origin in dev —
// cookies and relative fetches behave exactly as they will in production,
// where the built assets are served behind the same host as the API.
const API_PREFIXES = [
  "/auth",
  "/shell",
  "/home",
  "/records",
  "/preferences",
  "/schema",
  "/theming",
  "/workprocesses",
  "/help",
  // The mentor-facing engagement surfaces (PI-010, api/routers/mentoring.py).
  "/engagements",
  "/sessions",
  "/email",
  "/resources",
  // The panel catalog + leadership reporting (WTK-233 / WTK-171).
  "/panels",
  "/leadership",
  "/openapi.json",
];

const API_TARGET = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  test: {
    // e2e/*.spec.ts belongs to Playwright (npm run e2e); vitest importing
    // it trips @playwright/test's "not run by the runner" guard.
    exclude: ["e2e/**", "node_modules/**"],
  },
  server: {
    proxy: Object.fromEntries(
      API_PREFIXES.map((p) => [
        p,
        {
          target: API_TARGET,
          // A browser NAVIGATION under an API prefix is an app route, not an
          // API call — /records/:type/:id is the pop-out window (app.tsx).
          // Serve the SPA for document requests; proxy everything else.
          bypass: (req: import("node:http").IncomingMessage) =>
            req.headers.accept?.includes("text/html") ? "/index.html" : undefined,
        },
      ]),
    ),
    // The Playwright run (playwright.config.ts sets MENTORAPP_E2E) needs no
    // hot reload, and CI-class hosts can exhaust inotify instances under the
    // watcher — E2E serves without watching instead of failing to boot.
    ...(process.env.MENTORAPP_E2E ? { watch: null } : {}),
  },
});
