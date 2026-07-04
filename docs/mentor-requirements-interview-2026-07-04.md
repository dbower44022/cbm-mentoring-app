# Mentor user-requirements interview — working notes (2026-07-04)

Captured live from Doug per the interviewer charter. Scratch — the DB is the
source of truth once consolidated (session SES-002 / conversation CNV-002,
Mentoring Domain topic).

## Login landing / engagement triage (dictated)

- Left nav lists seven areas: Contacts, Companies, Clients, Engagements,
  Sessions, Resources, Events.
- On login: Engagements area selected, "My Active Engagements" view shown.
- View filter: status IN (Active, Pending Acceptance, On Hold, Assigned,
  Dormant).
- Columns: engagement name, status, primary contact name, primary contact
  email, last session date, next session date, total sessions.
- Click engagement -> preview shows full detail: notes + open action items.
- Engagement lists client, company, contacts; each click-through to a
  detail pop-up.
- Purpose: mentor decides which engagement needs action next.

New concepts surfaced (to define): action items; sessions with next/last
dates (scheduling exists); Resources area; Events area; Clients vs Contacts
vs Companies distinction; engagement status vocabulary (Active, Pending
Acceptance, On Hold, Assigned, Dormant).

## Triage logic (dictated)

Priority order when scanning My Active Engagements:
1. Status = Pending Acceptance -> mentor must decide accept/decline.
2. Else: when is the next session? If imminent -> open the session, START
   THE VIDEO CONFERENCE, prepare for the client to join.
3. Else: work open action items in preparation for the next session.

New concepts: sessions are conducted via video conference started from the
session record; session prep is a distinct activity; action items are
prep-oriented tasks.

## Video conference link (dictated)

- Session record has a "Conference Link" field (Zoom or Google Meet URL).
- Start conference = launch the link (app does NOT host video).
- Baseline (must): user creates the meeting externally and pastes the link.
- Ideal (enhancement): entering session date/time makes the app create the
  meeting via the Google or Zoom API and fill the field automatically.

## Session prep & conduct surface (dictated)

- Data-dense view: engagement history + status, summary of ALL engagement
  notes, and full session history — quick memory refresh after weeks of no
  contact.
- Note-taking + action-item entry available live during the call, or
  shortly after it ends.
- (Open, later: notes exist at engagement level and session level — clarify
  the relationship when consolidating.)

## Session wrap-up flow (dictated)

1. At call close: enter next session date/time -> system sends the CLIENT a
   meeting invite with the video link (scheduling sends outbound invites —
   not just a date field).
2. After call: review the AI call transcript; compose a summary into
   session notes + action items.
3. Update contact/company info collected on the call.
4. Move to next engagement.

New scope: AI call transcript in the loop (source TBD); outbound calendar
invites to clients.

## Transcript/summary automation (suggestion ruled IN)

- The app-created meeting is the transcript source: app fetches the
  transcript via the Zoom / Google APIs when ready, attaches it to the
  session, and an AI pass pre-drafts the session summary + suggested action
  items for mentor review/edit (mentor stays the author).
- Fallback (and non-app-created meetings): paste the transcript. Fathom out
  of the automated path for now.
- Credentials: org-level, admin-granted once (Zoom server-to-server app on
  the CBM account; Google Workspace service account with domain-wide
  delegation). No per-user credentials.
- OPEN ISSUE: confirm mentors host Zoom calls under the CBM org Zoom
  account (personal accounts would force per-user OAuth; automated path is
  scoped to org-hosted meetings).

## Acceptance flow (dictated)

- Pending Acceptance: mentor reviews engagement info, judges fit vs skills.
- Accept = set status to "Assigned".
- First steps after accepting: send intro email to client + schedule first
  session.
- Email sent FROM the app, selected from a list of templates (template
  library; admin/merge-field details TBD).

## Decline (dictated)

- Decline = set status to "Assignment Declined". No reason captured in this
  version (ruled: not helpful). Confirmed requirement amended via change
  decision + re-approval (status vocabulary: accept -> Assigned,
  decline -> Assignment Declined).

## Action items (dictated)

- v1: a rich-text box of bulleted action items (no structured task
  records). "Open action items" in triage = reading this text.
- Future (only if super easy): full task list per engagement.
- AI wrap-up pre-draft feeds suggested action items into this text for
  mentor edit.

## Notes/action-items data model (dictated)

- Entered at the SESSION level (notes + action items per session).
- ENGAGEMENT shows an aggregated rollup of all sessions' notes and action
  items (read view — no need to open sessions individually).

## Resources (dictated)

- A library of documents, videos, training materials, and links shared
  with clients.
- Sharing = email the client a link to the resource, from the app.
- Staff maintain the library; mentors consume and share.

## Events (dictated)

- Staff-defined events: meetings, training sessions for mentors and
  clients. Reference-only in the app.

## Company subclassing (dictated)

- Company = generic organization.
- Client = subclass of Company + client-specific fields (added when they
  engage in mentoring).
- Partner = subclass of Company for mentoring-delivery partners.

## Status semantics (dictated)

- On Hold = client-requested pause.
- Dormant = no response from client.

## CONSOLIDATED (2026-07-04)

Sixteen child requirements (REQ-071..086) created under the Mentoring Domain
topic with interview provenance and refines edges; Doug confirmed all 16
(DEC-072). REQ-066 amended earlier in the interview (DEC-071 — decline is a
status change only).

Release finding: scope additions cannot enter a frozen release (amend
window covers requirement amendments only). Governed correction path used:
REL-002 "CBM Mentoring App v1 (r2)" opened as a correction of REL-001,
REL-001 superseded, PRJ-001 re-scoped, PI-010 extended with the 16 new
implements edges, REL-002 frozen (reconciliation / amend_window).

Open issues for design: (1) confirm mentors host Zoom under the CBM org
account; (2) presentation form of the consolidated notes summary on the
session prep surface.
