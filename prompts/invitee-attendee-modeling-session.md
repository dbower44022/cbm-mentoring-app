# Session prompt — invitee-vs-attendee modeling for mentoring sessions

Start in ~/Dropbox/Projects/crmbuilder; bootstrap per its CLAUDE.md (cloud
store https://api.crmbuilder.ai, header `X-Engagement: ENG-004`). This is a
PLANNING session: the deliverable is a ruled design recorded as governance,
not code. DEC-095 operating rules bind — no agent spawns, no metered API,
all work in-session under Doug's eyes.

## Why this session exists

The session details surface (REQ-110, built under PI-015, approved by
DEC-098) shows an attendee grid with a per-person Invited/Attended status
column. That column is currently a DERIVED STOPGAP, ruled in DEC-098: the
grid lists the engagement's mentor and primary contact, and participation
reads off the session status — everyone "Invited" until the session is
marked completed, everyone "Attended" after. It cannot represent a no-show,
a third participant, or an invitee list chosen at scheduling. Doug directed
(2026-07-12) that the real modeling get its own planning session — this one.

## The facts on the ground (verified 2026-07-12)

- **App side (`cbm-mentoring-app`):** sessions are application-owned rows
  (`storage/mentoring.py MentoringSession`) with NO participant columns.
  The REQ-078 invite is emailed to the engagement's PRIMARY CONTACT at
  scheduling; its sent/skipped outcome is returned in `meta` and logged,
  never persisted. Engagements carry exactly one denormalized contact
  (name/email/CRM id) — there is no app-side contact list or contact
  entity. `automation/contact_detail.py` is the read seam for CRM contact
  detail (dev default deterministic; production CRM binding not yet built).
- **CRM side (CBM EspoCRM, `ClevelandBusinessMentors` repo,
  `programs/MN/MN-Session.yaml`):** the CRM Session entity carries TWO
  manyToMany contact links — `mentorAttendees` and `clientAttendees`,
  deliberately role-split per SES-DEC-009, each workflow-enforced to at
  least one contact when a session completes.
- **No bridge:** app sessions have no CRM counterpart and no sync — the
  CRM's attendee links cannot answer for app-created sessions.

## Questions the session must rule on (with Doug)

1. **Where does session-participant truth live?** App-owned attendee rows
   (REQ-063 posture: mentoring-process data is app-owned), a session
   write-through to the CRM Session entity (REQ-062 posture: staff
   visibility), or app-owned with CRM export later. This is the load-bearing
   decision; everything below follows it.
2. **The invitee list at scheduling.** Today "who to invite" collapses to
   the primary contact. Multiple invitees implies an engagement-contacts
   model the app does not have — scope that honestly (CRM contact picker via
   the detail seam? app contact refs?).
3. **Invite outcome persistence.** Store sent/skipped-with-reason per
   invitee on the session (today it is log-only).
4. **Attendance capture.** Where the mentor marks who actually attended —
   the prep/conduct surface at session end is the natural home (REQ-082
   flow); define the gesture and whether marking is required to complete.
5. **No-shows.** Invited-but-absent must be representable and reportable
   (leadership reporting REQ-070 will want it).
6. **Roles.** Preserve the SES-DEC-009 mentor/client role split in whatever
   the app models (one table with a role column matches the shipped grid).
7. **Surface updates.** REQ-110's grid then reads stored participation
   instead of the derivation; the DEC-098 stopgap language is superseded by
   the new ruling.

## Deliverables

- A decision recording the ruled design (supersedes the DEC-098 attendee
  stopgap paragraph), authored with Doug.
- New/updated requirement(s) per SKL-102 (readability gate; statement free
  of identifiers; verifiable acceptance summary), confirmed by Doug, edged
  to TOP-011 and the approving decision.
- A planning item (executable only when Doug says build) implementing the
  ruling, edged `planning_item_implements_requirement` and into PRJ-001.
- If CRM-side changes are ruled in (write-through/sync), file the
  corresponding item in the CBM repo's governance, not here.

Read REQ-110, DEC-098, and PI-015 from the store before proposing anything.
