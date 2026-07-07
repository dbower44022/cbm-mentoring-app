/**
 * Wire shapes for the mentor-facing engagement surfaces (PI-010,
 * WTK-168/169/177/179), following the api/payloads.ts precedent:
 * hand-maintained mirrors of the Python view-models
 * (api/routers/mentoring.py serves these exact camelCase names) until the
 * generated schema covers envelope payloads.
 */

/** One engagement as the mentoring surfaces speak it (status by NAME). */
export interface EngagementPayload {
  engagementID: string;
  engagementName: string | null;
  engagementStatus: string | null;
  engagementStatusLabel: string | null;
  engagementSummary: string | null;
  primaryContactName: string | null;
  primaryContactEmail: string | null;
  primaryContactCrmID: string | null;
  crmEngagementID: string | null;
  rowVersion: number;
}

/** One session entry of the rollup / session history (newest first). */
export interface RollupSessionPayload {
  sessionID: string;
  scheduledAt: string;
  sessionStatus: string | null;
  sessionStatusLabel: string | null;
  conferenceLink: string | null;
  sessionNotes: string | null;
  actionItems: string | null;
  rowVersion: number;
}

/** The engagement's client role + company anchor (REQ-086 subclassing). */
export interface RollupClientPayload {
  clientID: string;
  clientSince: string | null;
  clientProgram: string | null;
  clientReferralSource: string | null;
  clientStage: string | null;
  crmCompanyRefID: string | null;
  crmCompanyID: string | null;
}

/** One engagement-designated contact (click-through target, REQ-074). */
export interface RollupContactPayload {
  contactName: string | null;
  contactEmail: string | null;
  crmContactID: string | null;
}

/** `GET /engagements/{id}/rollup` — the one read behind preview AND prep. */
export interface EngagementRollupPayload {
  engagement: EngagementPayload;
  client: RollupClientPayload | null;
  contacts: RollupContactPayload[];
  stats: {
    totalSessions: number;
    heldSessions: number;
    firstSessionAt: string | null;
    lastSessionAt: string | null;
    nextSessionAt: string | null;
  };
  /** Sessions carrying notes or action items, NEWEST FIRST (REQ-073/074). */
  rollup: RollupSessionPayload[];
  /** Every live session, newest first — history + prep navigation. */
  sessions: RollupSessionPayload[];
}

/** `POST /engagements/{id}/lifecycle` success body (REQ-075/REQ-076). */
export interface LifecycleResultPayload {
  engagement: EngagementPayload;
  transition: string;
  confirmation: string;
  nextSteps: { key: string; label: string; templateKey?: string }[];
}

/** One staff-maintained template (`GET /email/templates`, REQ-077). */
export interface EmailTemplatePayload {
  templateKey: string;
  templateName: string;
  mergeFields: string[];
}

/** `POST /email/send` / `POST /resources/{id}/share` — preview OR sent. */
export interface EmailSendPayload {
  templateKey: string;
  to: { address: string; name: string };
  subject: string;
  body: string;
  sent: boolean;
  confirmation: string | null;
}

/** One created/patched session as `serialize_record` flattens it. */
export interface SessionRecordPayload {
  sessionID: string;
  engagementID: string;
  scheduledAt: string;
  conferenceLink: string | null;
  sessionNotes: string | null;
  actionItems: string | null;
  rowVersion: number;
}
