import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

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
  "/openapi.json",
];

const API_TARGET = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(API_PREFIXES.map((p) => [p, { target: API_TARGET }])),
  },
});
