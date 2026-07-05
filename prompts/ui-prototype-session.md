# Session prompt — ENG-004 UI prototype gate (interactive, no metered spend)

Continue the ENG-004 "CBM Mentoring Custom App" end-to-end test — UI
PROTOTYPE session. Start in ~/Dropbox/Projects/crmbuilder (its CLAUDE.md
governs). Bootstrap per that CLAUDE.md (cloud API, X-Engagement: ENG-004,
TOP-013 + governance rules + preferences). Read
~/Dropbox/Projects/cbm-mentoring-app/prompts/eng-004-kickoff.md for history.

## Context (2026-07-05 pivot — see the halt decision in the store)

Doug halted autonomous metered agent runs on cost: the dev-lane pilot's
first UI cost ~$300 unmetered and underwhelmed. New operating rules:
NOTHING runs against the metered Anthropic API — no ADO runs, no SDK agent
calls; local work and subscription subagents only. The engagement is
retro-fitting a prototype gate (candidate platform requirement exists)
before any further build spend.

## Today: build and review the clickable UI prototype

1. Read from the store: the confirmed UI requirements (REQ-009..046 area
   sets), the mentor interview requirements (REQ-071..086), and the UI
   standards skills SKL-111..117 (grid, layout, forms, workprocess, help,
   look & feel). These are binding; do not improvise beyond them.
2. Build a SELF-CONTAINED static clickable prototype (plain HTML/CSS/JS,
   no build step, no framework, fake data) of the four load-bearing
   screens: (a) My Active Engagements triage grid with action bar, status
   bar, preview pane; (b) engagement preview with notes/action-items
   rollup and click-through pop-ups; (c) the session prep/conduct surface
   (dense refresh + notes + conference link); (d) Home panel with admin
   messages and navigation. Put it in cbm-mentoring-app/prototype/ (it is
   a review artifact, not app code — mark it as such in the commit).
3. Walk Doug through it screen by screen per his dictation method: capture
   every reaction as a ruling; corrections become requirement change
   decisions (governed change path) or new child requirements; look-&-feel
   reactions may amend the UI standards skills (re-record to the DB).
4. Record the prototype review as a decision under ENG-004 (the
   retro-fitted prototype gate), with the corrections list.
5. Close out: session/conversation records, kickoff DONE section, commit
   (doc/prototype only; Governed-By: trivial + Exemption-Reason).

Cost rule for this session: $0 metered. Subscription subagents allowed.
