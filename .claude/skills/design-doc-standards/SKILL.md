---
name: design-doc-standards
description: Required structure for the UX design (docs/ux-design.md) and technical design (docs/technical-design.md), including how both trace to MENT requirement IDs. Load before writing either design document.
---

# Design document standards

Both design docs are contracts consumed by the build-phase agents as written.
Every section that satisfies a requirement cites its `MENT-###` ID; end each
doc with a **traceability table** (MENT ID → sections covering it → "not
covered because…"). An uncovered Must-have fails the design gate.

## UX design (`docs/ux-design.md`)

1. **Personas** — one paragraph each, grounded in the spec's actors.
2. **User journeys** — per actor, the end-to-end flows (numbered steps),
   citing the MENT IDs each step satisfies.
3. **Screen inventory** — every screen/page: purpose, who reaches it and how,
   what it shows, what actions it offers.
4. **Wireframes** — per screen, low-fi layout as an ASCII/text sketch or
   simple HTML. Content and hierarchy, not pixels. Reference `ui-standards`
   components by name (e.g. "standard grid: columns …, filters …, default
   sort …, empty state: '…'") and specify only the per-screen particulars —
   never respecify standard behavior.
5. **Interaction & state rules** — empty states, loading, errors, validation
   messages, permission-denied behavior.
6. **Assumptions / Questions for the stakeholder**
7. **Traceability table**

## Technical design (`docs/technical-design.md`)

1. **Overview & stack decision** — house stack per `webapp-standards`, or a
   justified deviation.
2. **System context** — the app, its users, and every external system
   (EspoCRM, email, hosting), with what data flows where and which system owns
   which truth.
3. **Data model** — entities, fields, keys, relationships; what lives in the
   app's store vs. the CRM; migration/versioning approach.
4. **API design** — endpoints: method, path, auth, request/response shapes,
   error cases. Cite MENT IDs per endpoint.
5. **Auth & permissions** — who can do what, enforced where.
6. **Module/package layout** — the intended source tree with one line per
   module.
7. **Build plan** — ordered vertical slices for Phase 3, each slice = one
   user-visible capability, its MENT IDs, and which agents build it. Slice 1
   must produce a bootable, deployable skeleton.
8. **Risks & open decisions**
9. **Assumptions / Questions for the stakeholder**
10. **Traceability table**

## Shared rules

- Design for the spec's volume honestly — this is a small nonprofit tool, not
  a hyperscale system; complexity must earn its place.
- Neither doc restates requirements — it cites them. If a requirement is
  wrong or missing, propose the change; don't design around it silently.
- Wireframes and API shapes are binding on the developers; mark anything
  intentionally left to implementer judgment as `(implementer's choice)`.
