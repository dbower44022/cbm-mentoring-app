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
  /** Non-null = an app-created org meeting exists (REQ-080) — the platform
   * can be asked for its transcript; null = the link was pasted (REQ-079). */
  externalMeetingID: string | null;
  /** Where the attached transcript came from (WTK-182 provenance). */
  transcriptSource: string | null;
  /** The REQ-083 PROPOSALS awaiting the mentor's review — never auto-applied. */
  draftSummary: string | null;
  draftActionItems: string | null;
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
  externalMeetingID: string | null;
  transcriptSource: string | null;
  draftSummary: string | null;
  draftActionItems: string | null;
  rowVersion: number;
}

/** One derived attendee row on the session details read (REQ-110, DEC-098). */
export interface SessionAttendeePayload {
  name: string;
  role: string;
  companyName: string | null;
  companyRefID: string | null;
  crmContactID: string | null;
  email: string | null;
  phone: string | null;
  participation: string;
}

/** `GET /sessions/{id}/detail` — the one session-details read (REQ-110).
 * The transcript TEXT is deliberately absent: `transcript` carries only the
 * state triple the section renders from; the text rides the dedicated
 * on-demand read below. */
export interface SessionDetailPayload {
  session: RollupSessionPayload;
  engagement: { engagementID: string; engagementName: string | null };
  client: {
    clientID: string;
    crmCompanyRefID: string | null;
    crmCompanyID: string | null;
  } | null;
  attendees: SessionAttendeePayload[];
  transcript: { state: string; source: string | null; wordCount: number };
}

/** `GET /sessions/{id}/transcript` — the on-demand transcript text (REQ-110). */
export interface SessionTranscriptPayload {
  state: string;
  transcriptText: string | null;
  transcriptSource: string | null;
}
