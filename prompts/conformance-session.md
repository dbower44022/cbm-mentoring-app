# Session prompt — FND-909 rendered-conformance fixes (interactive)

Start in ~/Dropbox/Projects/crmbuilder; bootstrap per its CLAUDE.md (cloud
API, X-Engagement: ENG-004). Read prompts/eng-004-kickoff.md for history.
Mode per Doug's rulings: INTERACTIVE — no ADO runs, no agent fan-out except
single well-scoped subagents verified independently; every UI fix is
screenshot-verified against cbm-mentoring-app/prototype/ (DEC-084) before
any completion claim. That rendered gate is FND-909's lesson and ENG-001
candidate REQ-473.

## State (2026-07-07 night)

- PI-010 FROZEN back to In Progress (FND-909 blocking gap). All other PIs
  Resolved except PI-005/PI-006 (In Review, batch pending — review AFTER the
  conformance pass; their surfaces need the same rendered scrutiny).
- Defect sweep: scratchpad defect-sweep-2026-07-07.md, mirrored below.
  CLUSTER 1 FIXED + verified + committed (42f6673): D3 preview overlay,
  D4 preview follows selection, D5 stale preview after lifecycle,
  D6 action-bar anatomy (two common buttons + floating Other Actions menu).
- REMAINING: cluster 2 = D1 raw datetimes in grid values (/panels rows have
  no format layer — SKL-112 column format), D2 UUID lead column (drop from
  view), D11 headers/aggregates footer, D12 status-bar polish; cluster 3 =
  D7 status chips/conditional formatting (REQ-045; theming slots exist);
  cluster 4 = D8 Home unreachable (startup redirect fires every "/" load,
  no Home nav area — REQ-011), D9 stale session 500s instead of 401 re-auth;
  cluster 5 = D10 prep-surface splitters/zoom (REQ-087).
- Demo: uv run uvicorn tests.e2e_harness:app --port 8000 (in repo root) +
  cd frontend && CHOKIDAR_USEPOLLING=true npm run dev -- --port 5173.
  Login frank / any password (janet = leadership). Chrome tab was driving
  http://127.0.0.1:5173.
- After all clusters: rewrite the Playwright journeys to assert the ruled
  anatomy (the rendered gate, permanent), THEN batch review PI-005/006/010
  with Doug EYES-ON (demo walkthrough, not digest-only), then REL-003
  QA/test/ship gates.
- Morning addition: regression test for usePanelChrome identity stability (render probe re-rendering with fresh session objects; assert single load)
