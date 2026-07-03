---
name: webapp-standards
description: House stack and engineering conventions for CBM web applications. Load before making technology choices (solution-architect) and before writing any application code (backend-developer, frontend-developer, qa-engineer).
---

# CBM web application standards

The house stack, proven in production by `cbm-client-intake`. Deviate only
with a written justification in the technical design (the stakeholder decides
at the gate) — "I prefer X" is not a justification; "the requirement cannot be
met with the house stack because…" is.

## Stack

- **Backend:** Python 3.12+, FastAPI, Pydantic v2, `pydantic-settings` for
  config. Dependency management with **uv** (`uv sync`, `uv run`).
- **Persistence:** Postgres via SQLAlchemy (async) + Alembic migrations.
  Local dev via docker-compose. The app must also boot with no database when a
  feature can degrade gracefully.
- **Frontend:** server-served plain HTML/CSS/vanilla JS — **no build step, no
  framework** — unless the UX design genuinely requires client-side state that
  vanilla JS cannot carry cleanly (justify in the design).
- **Tests:** pytest (+ httpx TestClient for API tests).
- **Deploy target:** Docker on DigitalOcean App Platform; one container,
  `/healthz` endpoint reporting `{status, version, environment}`.

## Conventions

- **Zero-env-var boot:** every setting defaults; the app starts in a safe
  dev/dry-run mode with no configuration. Secrets only via environment
  variables — never in code, never committed.
- **Config lives in one module** (`core/config.py` pattern); credentials for
  external systems (e.g. EspoCRM) are held in one client module only.
- **External calls are best-effort or durable — never silently lossy.** A
  failed side-effect (CRM write, email) must be visible: logged with payload,
  retried, or surfaced to the user.
- **EspoCRM integration** (when the design uses it): REST API with
  `X-Api-Key` (service) or `Espo-Authorization` token (per-user); phones
  normalized to E.164; enum values validated against live metadata before
  writes (drifted values dropped + noted, not fatal); staff auth = EspoCRM
  credentials → signed session cookie, gated by Team membership (not Role —
  regular users cannot read their own role names).
- **Idempotency:** any endpoint that creates records accepts a client
  submission token and dedupes on it.
- **Accessibility:** semantic HTML, labels on every input, keyboard-navigable
  forms, visible focus.
- Conventional Commits; version in `pyproject.toml` surfaced at `/healthz` and
  the page footer.

## Code quality bar

- Type hints on public functions; Pydantic models at every trust boundary.
- No dead code, no commented-out code, no TODOs without an issue/ID.
- Tests accompany the change-set that introduces the behavior — a slice is not
  done until its MENT acceptance criteria have passing tests.
