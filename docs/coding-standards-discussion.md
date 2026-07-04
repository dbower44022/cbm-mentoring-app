# Coding standards for maintainability — decision record (ENG-004)

Working session 2026-07-04. Doug dictates; rulings captured live; this file
is authoring scratch — the DB (engagement-scoped instruction skills bound to
the developer agent profiles) is the source of truth once consolidated.

## CS-1 — Logging & exception handling (Doug anchor; suggestion ruled IN)

- Base layer: structured JSON logging to stdout only (App Platform captures
  it); one shared logger; every line carries request ID, user ID, and
  entity/record context where present. No log files, no shipping.
- Exceptions: instrument with the standard Sentry SDK; unexpected
  exceptions captured with request context + release version.
- Backend: GlitchTip (OSS, Postgres+Redis, Sentry-SDK-compatible) — may
  start on Sentry SaaS free tier and move; code never knows the backend.
- Rejected: build-our-own aggregation; self-hosted Sentry (too heavy);
  Datadog (cost); PostHog (analytics, not error tracking).
- Discipline: ALL error reporting via SDK + structured logger; no bare
  print; no swallowed exceptions.

## CS-2 — Product analytics as maintenance (suggestion ruled IN — "keep it simple")

- In-app usage events: one table (user, panel/view, action, timestamp),
  emitted via ONE shared capture primitive on every panel open and action
  invocation — never ad-hoc.
- Read through the app's own admin data-source/view/dashboard system; no
  third service; data stays home.
- Retention: usage events get a trim job like other operational tables.
- Escalation path only if needed later: PostHog SaaS free tier (additive);
  self-hosted PostHog ruled out (ClickHouse weight).

## CS-4 — User testing (DEFERRED)

- Suggestion on the table (PostHog session replay w/ privacy masking +
  built-in Send Feedback action; OpenReplay self-hosted as the
  data-at-home fallback) — Doug deferred the whole area to later.

## CS-3 — Automated UI testing (ruled IN)

- Playwright for end-to-end UI tests (auto-wait, traces on failure,
  multi-window/pop-out support Cypress lacks, headless CI).
- Scope pyramid: shared engines (grid, forms, validation, concurrency) get
  dense component tests ONCE at platform level; Playwright covers user
  journeys (login -> triage -> accept -> schedule -> session -> notes ->
  wrap-up) as a small fast smoke suite gating every deploy. No
  test-per-screen (screens are config over engines).
- Traceability: every requirement acceptance criterion maps to >=1
  automated test.
- Flake rule: fix or quarantine same-day.
- Data rule: seeded local Postgres + stubbed CRM per-commit; scheduled
  integration suite against crm-test; never production.

## CS-5 — Strict typing + mechanical style gates (ruled IN)

- Strictest practical static typing (TS strict / pyright strict; no untyped
  modules; no `any` without inline justification).
- Opinionated auto-formatter — style never authored or reviewed by humans.
- Linter at zero warnings.
- All three are CI merge gates: failing = not merged, no exceptions.

## CS-6 — Boring-dependency policy (ruled IN)

- Platform/stdlib first, then the boring incumbent; a dep must be actively
  maintained, widely adopted, and replace meaningful code (~200-line rule).
- Every dependency gets a named reason recorded at adoption.
- Lockfile pinning; scheduled automated update PRs merged only on green CI.
- No load-bearing deps on abandoned/hobby projects. One framework per
  layer — no parallel ways to do the same thing.

## CS-7 — Engine-and-configuration organization (ruled IN)

- Codebase = shared platform engines (grid, forms, validation, list-read,
  jobs, email, sync) + thin per-feature configuration; new features are
  mostly declaration.
- Feature code never re-implements engine capability — extend the engine,
  no local workarounds. One canonical home per concept; duplication is a
  defect.
- No dead code, no commented-out code (version control is the archive).
- Size discipline as review signal (file past a few hundred lines /
  function past a screen -> split or justify), not a hard cap.

## CS-8 — Change discipline (ruled IN, governance-welded)

- Small single-purpose change-sets; PR-only to main; CI green required
  (types, lint, format, component suites, Playwright smoke, schema/registry
  migration check). Real review on every change-set (reviewer agent applies
  these standards; Doug at phase gates). No direct pushes, no red merges,
  no fix-in-next-PR. Plain-language what/why descriptions.
- Governance weld: every change-set traces release -> PI -> work task ->
  PR; commits carry Governed-By: PI-NNN; PR gate + trailer gate together
  enforce frozen release scope. Main always DEPLOYABLE; the release decides
  when to DEPLOY. Model A intact (branches carry only code). App repo gets
  the governance-gate hook on day one; PR-only is deliberately stricter
  than the crmbuilder repo's own direct-to-main practice.

## CS-9 — Documentation (ruled MODIFIED: NO MD)

- NO markdown documentation files, anywhere in the repo.
- Documentation is data: a module/platform log in the V2 governance store —
  per-module/engine entries (ownership, contract, extension guide) plus the
  operational runbook — versioned, in a centralized accessible UI.
- Decisions remain DB decision records. Code comments only for constraints
  the code can't express.
- V2-side capability logged as an ENG-001 candidate requirement (module/
  platform documentation log + UI) to flow through V2's own
  requirement-first pipeline; candidate created 2026-07-04.
- Doc-currency rule carries over: a behavior change updates the module's
  log entry in the same change-set (review-checked).

## CS-10 — Code commenting (ruled IN — "exactly what I wanted")

- Comments carry the WHY; code carries the WHAT. Narration rejected in
  review; fix names/structure instead.
- Mandatory why-comments: constraints code can't express, non-obvious
  couplings, deliberate deviations, and external-system quirks AT the code
  site (EspoCRM header-only errors etc.).
- Public surfaces (exported functions, engine APIs, endpoint handlers) get
  contract docstrings (inputs/outputs/failure modes), linter-enforced.
- No orphan TODOs — every TODO names a tracked planning item or finding.
- Density is a smell: sparse, load-bearing comments are the target.

## Round complete 2026-07-04 — CONSOLIDATED TO THE DB

Recorded as ENG-004 instruction skills, bound to ALL FOUR build-area triads
(storage AGP-001/002/006, api AGP-010/011/012, ui AGP-016/017/018, espo
AGP-025/026/027 — 36 bindings):

- **SKL-121** — observability & usage analytics standard (CS-1, CS-2)
- **SKL-122** — code maintainability standard (CS-5, CS-6, CS-7, CS-9,
  CS-10)
- **SKL-123** — testing & change discipline standard (CS-3, CS-8)

Governance: **DEC-073 (ENG-004, Active)**. Contracts verified carrying all
three per triad; ENG-001 isolation confirmed (note: the contract endpoint's
?engagement= param is overridden by the X-Engagement header — verify
isolation with the header, not the param; flagged as an API finding
candidate for ENG-001).

Deferred: CS-4 user testing (PostHog session replay + in-app feedback
proposal on the table). Cross-engagement: module/platform documentation log
logged as ENG-001 candidate requirement REQ-455.
