# Domain brief — the CBM mentoring process

Seed knowledge for the agent pipeline. This is the **input** to Phase 1
(requirements); it describes the domain and current landscape, and deliberately
does NOT specify the application. Distilled 2026-07-03 from the production
`cbm-client-intake` system, the EspoCRM data model, and the MN-* process
definitions.

## The organization

**Cleveland Business Mentors (CBM)** is a nonprofit that matches volunteer
business mentors with small-business owners (clients) for ongoing mentoring
engagements. **EspoCRM** (a customized instance) is the current system of
record; a custom FastAPI application (`cbm-client-intake`) provides public
intake forms and staff tools on top of it.

## Actors

- **Prospective mentor** — applies via the public volunteer form.
- **Mentor** — an approved volunteer with a CBM login and `@cbmentors.org`
  email; has a capacity (max concurrent clients) and an accepting-new-clients
  flag.
- **Client** — a small-business owner who requested mentoring via the public
  intake form.
- **Mentor Administration staff** — review mentor applications, complete mentor
  records, approve/activate mentors.
- **Client Administration staff** — review submitted engagements and assign
  mentors to clients.
- **Leadership** — needs visibility: pipeline health, active engagements,
  outcomes.

## The mentoring lifecycle (as it exists today)

### 1. Mentor onboarding
Volunteer applies → Contact + CMentorProfile created (`mentorStatus=Candidate`).
Staff review in a roster tool: a record is **Complete** when a Contact is
linked, ethics/training/terms sign-offs are true, and (if Active) the mentor
has a CBM email + linked login User. Approval (`Approved`/`Active`) provisions
an EspoCRM login and optionally a Google Workspace mailbox. Active mentors with
`acceptingNewClients=true` and spare capacity are assignable.

### 2. Client intake
Client submits the intake form → Account + Contact + CClientProfile +
CEngagement (`engagementStatus=Submitted`) created. The engagement carries the
client's request: business stage, industry, mentoring focus areas.

### 3. Assignment
Staff view Submitted engagements, pick an eligible mentor, assign → engagement
gets the mentor (`mentorProfile`), status → `Pending Acceptance`, and the
mentor's User is assigned across the related records.

### 4. The engagement itself — **largely unsupported by software today**
After assignment, the process runs on email/phone/spreadsheets:
- Mentor acceptance or decline of an assignment (status exists; no mentor-facing
  tool).
- First-meeting scheduling, ongoing session scheduling.
- Session logging: date, duration, topics, notes, next steps.
- Progress against the client's goals; changing focus areas.
- Pausing, reassigning, or ending an engagement; capturing outcomes.
- Mentor capacity bookkeeping (currently computed fields on the profile).
- Reporting: sessions delivered, active engagements, mentor utilization,
  client outcomes.

**This gap — step 4 — is the primary opportunity space for the new
application.** Steps 1–3 have working staff tools; the agents should treat
replacing them as out of scope unless a requirement genuinely demands it, but
the new app must interoperate with the records they produce.

## Current data model (EspoCRM, simplified)

- `Contact` — people (mentors, clients, partners…), typed by `cContactType`.
- `Account` — companies, typed by `cAccountType`.
- `CMentorProfile` — mentor record: status (Candidate/Approved/Active/…),
  accepting flag, capacity (`maximumClientCapacity`, `currentActiveClients`,
  `availableCapacity`), expertise (`areaOfExpertise`), industry experience,
  compliance sign-offs, linked Contact + login User.
- `CClientProfile` — client business profile (stage, employees, formation).
- `CEngagement` — the mentoring engagement: status (Submitted / Pending
  Acceptance / …), linked client profile, contacts, organization, and assigned
  `mentorProfile`.
- `CIntakeSubmission` — audit log of every form submission.

There is **no session/meeting entity** today — session tracking has no system
of record.

## Constraints and open questions (for the requirements phase to resolve)

- **System of record:** EspoCRM holds mentors/clients/engagements. The new app
  could (a) read/write EspoCRM via its REST API (the pattern proven in
  cbm-client-intake), (b) own new data (e.g. sessions) in its own store while
  referencing CRM records, or (c) a hybrid. This is a design decision — but any
  answer must not fork the truth for data the CRM already owns.
- **Auth:** mentors have EspoCRM logins (provisioned at approval); staff have
  EspoCRM logins gated by Teams. The proven pattern is EspoCRM-credential login
  with a signed session cookie. Clients have **no** login today.
- **Hosting:** CBM deploys on DigitalOcean App Platform (Docker from GitHub,
  managed Postgres). Ops capacity is one volunteer engineer — favor boring,
  low-maintenance choices.
- **Volume:** small — tens of mentors, low hundreds of engagements/year.
  Simplicity beats scale.
- Open: Should clients get a portal (see status, book sessions)? Who confirms a
  session happened? What outcome metrics does leadership actually need? What is
  the mentor-acceptance flow (deadline? auto-release?)? These are questions for
  the requirements analyst to answer with the stakeholder (Doug).
