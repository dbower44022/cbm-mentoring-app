# Session prompt — REL-004 reconciliation-driven completion (block builds)

Start in ~/Dropbox/Projects/crmbuilder; bootstrap per its CLAUDE.md (cloud
store, X-Engagement: ENG-004). History: prompts/eng-004-kickoff.md.

## BINDING OPERATING RULES (DEC-095 — non-negotiable)
- NO agent spawns of any kind. NO metered Anthropic API. ALL work is done
  locally, in-session, by the orchestrating context, under Doug's eyes.
- Requirements corpus is Doug's property: status changes ONLY via governed
  decision edges (the store enforces approval_required; never work around).
- Every UI change is RENDERED-VERIFIED (demo harness + real-input Playwright)
  before any completion claim. Synthetic-event checks are NOT verification
  for native browser behaviors. Always assert patch anchors (a silent no-op
  replace caused a false claim once — FND-909/REQ-108 incident).
- Extend frontend/e2e/journeys.spec.ts (the rendered-anatomy gate) with each
  block's assertions; 14+ journeys must stay green (fresh servers — never
  reuse the demo's mutated state for gate runs).

## STATE
- REL-004 frozen (reconciliation), corrects voided REL-003. Scope = EXACTLY
  the 2026-07-07 delivery reconciliation (33 REQs: 9 not-delivered + 24
  partial) — list in the REL-004 description and kickoff. Corpus: 96
  confirmed (DEC-095 reversal; FND-911).
- App: backend 1257 tests / frontend 145 / gate 14 journeys, all green.
  Demo: uvicorn tests.e2e_harness:app --port 8000 + cd frontend &&
  CHOKIDAR_USEPOLLING=true npm run dev -- --port 5173. Login frank/any
  (janet = leadership).

## BLOCK ORDER (Doug's plan; his eyes-on gate ends each block)
1. FORMS & EDITING (start here): REQ-032 full edit form, 037 create +
   duplicate detection, 035 per-field edit window, 036 lookups, 034 smart
   paste-parsing, 038 form keyboard, 039 read-only explanation, 040 field
   help hosting, 033 wiring registry validation to forms. SKL-114 (v2) is
   the binding standard; the rich-text seam precedent lives in
   mentoring/rich-text.tsx; the write engine + schema registry + duplicate
   shadow columns are delivered backend (PI-004/008).
2. GRID COMPLETIONS: 027 export/print, 029 ad-hoc filters, 031 state
   restoration, 017/018 user-view system + editor, 028 deep-link fallback,
   044 template UI + launch set, 046 contrast guardrail UI.
3. DOMAIN GAPS: 069 capacity flag, 067 duration/topics, 070 leadership
   dashboard render, 065 client profile depth, 068 focus-area revision,
   064 outage read-path educate.
4. SHELL COMPLETIONS: 010 pin management, 011 dashlet chooser + live view
   dashlets, 012 per-field window (lands with block 1), 013 same-user data
   sync, 014 >10s consumers (export progress), 015 pin-choice endpoints,
   003 logo, 054 field history, 055 real keyset on /panels.

Work per Doug's dictation method: he watches, rules land as REQ/SKL updates
via the governed path, block ends with his resolve decision.
