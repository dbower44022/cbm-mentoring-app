/**
 * Pure state for the session prep surface (WTK-177, REQ-081/082): which
 * session the surface conducts, and the notes/action-items entry lifecycle
 * (dirty tracking, save round-trip, refusals). Kept out of the component so
 * the vitest suite pins the behavior without a DOM.
 */

import { type ApiError } from "../api/envelope";
import { type RollupSessionPayload } from "./payloads";

/**
 * Which session prep conducts: the addressed one when the route names it;
 * otherwise the NEXT upcoming session (prep is a preparation surface — the
 * imminent session is the default subject); otherwise the newest session
 * (post-hoc note entry); null only when the engagement has no sessions.
 */
export function pickPrepSession(
  sessions: readonly RollupSessionPayload[],
  sessionId: string | undefined,
  now: Date = new Date(),
): RollupSessionPayload | null {
  if (sessionId !== undefined) {
    const addressed = sessions.find((s) => s.sessionID === sessionId);
    if (addressed !== undefined) {
      return addressed;
    }
  }
  // Sessions arrive newest-first; the LAST future entry is the soonest one.
  const upcoming = sessions.filter((s) => new Date(s.scheduledAt) > now);
  if (upcoming.length > 0) {
    return upcoming[upcoming.length - 1] ?? null;
  }
  return sessions[0] ?? null;
}

/** The entry region's state: current HTML vs the last-saved baseline. */
export interface PrepEntryState {
  sessionID: string;
  rowVersion: number;
  notes: string;
  actionItems: string;
  savedNotes: string;
  savedActionItems: string;
  saving: boolean;
  /** The save confirmation line, or null. */
  savedNotice: string | null;
  /** Refusal errors from the last save attempt (server's own words). */
  errors: ApiError[] | null;
  /** The standing REQ-083 proposals awaiting review; null = none. */
  draftSummary: string | null;
  draftActionItems: string | null;
  /** Bumped when a draft is inserted, so the editor reloads its content —
   * the ONLY time the surface rewrites the editable region (REQ-083: the
   * mentor's typing is never clobbered by automation). */
  notesRevision: number;
  actionItemsRevision: number;
}

export function loadedEntry(session: RollupSessionPayload): PrepEntryState {
  return {
    sessionID: session.sessionID,
    rowVersion: session.rowVersion,
    notes: session.sessionNotes ?? "",
    actionItems: session.actionItems ?? "",
    savedNotes: session.sessionNotes ?? "",
    savedActionItems: session.actionItems ?? "",
    saving: false,
    savedNotice: null,
    errors: null,
    draftSummary: session.draftSummary,
    draftActionItems: session.draftActionItems,
    notesRevision: 0,
    actionItemsRevision: 0,
  };
}

/** Unsaved entry exists — what the leave guard and Save button key on. */
export function isDirty(state: PrepEntryState): boolean {
  return (
    state.notes !== state.savedNotes || state.actionItems !== state.savedActionItems
  );
}

export type PrepEntryEvent =
  | { kind: "edited"; field: "notes" | "actionItems"; value: string }
  | { kind: "saveStarted" }
  | { kind: "saveSucceeded"; rowVersion: number }
  | { kind: "saveRefused"; errors: ApiError[] | null }
  /** A draft proposal is inserted into its entry field (REQ-083 accept-by-edit). */
  | { kind: "draftInserted"; field: "notes" | "actionItems" }
  /**
   * A server write OUTSIDE the save path landed (transcript retrieval, a
   * pasted transcript, a proposal dismissal): re-arm rowVersion and refresh
   * the proposals WITHOUT touching the typed entry or its baselines — an
   * external update never costs typed work.
   */
  | {
      kind: "externalUpdate";
      rowVersion: number;
      draftSummary: string | null;
      draftActionItems: string | null;
      notice: string | null;
    };

export function reducePrepEntry(
  state: PrepEntryState,
  event: PrepEntryEvent,
): PrepEntryState {
  switch (event.kind) {
    case "edited":
      // Editing again clears the stale confirmation, never the baseline.
      return {
        ...state,
        [event.field]: event.value,
        savedNotice: null,
      };
    case "saveStarted":
      return { ...state, saving: true, errors: null };
    case "saveSucceeded":
      // What was sent IS the new baseline; the fresh rowVersion arms the
      // next save (DB-S4 — every save carries the version it read).
      return {
        ...state,
        saving: false,
        rowVersion: event.rowVersion,
        savedNotes: state.notes,
        savedActionItems: state.actionItems,
        savedNotice:
          "Notes saved to this session — the engagement rollup reflects them immediately.",
        errors: null,
      };
    case "saveRefused":
      // The entry stays exactly as typed: a refusal never costs work.
      return { ...state, saving: false, errors: event.errors ?? [] };
    case "draftInserted": {
      // Appending, never replacing: the draft joins whatever the mentor has
      // typed, and the revision bump tells the editor to reload — the one
      // sanctioned rewrite of the editable region. Nothing is saved yet;
      // the mentor edits and saves as usual (author of record).
      const draft =
        event.field === "notes" ? state.draftSummary : state.draftActionItems;
      if (draft === null) {
        return state;
      }
      const merged = state[event.field] + draft;
      return {
        ...state,
        [event.field]: merged,
        notesRevision: state.notesRevision + (event.field === "notes" ? 1 : 0),
        actionItemsRevision:
          state.actionItemsRevision + (event.field === "actionItems" ? 1 : 0),
        savedNotice: null,
      };
    }
    case "externalUpdate":
      return {
        ...state,
        rowVersion: event.rowVersion,
        draftSummary: event.draftSummary,
        draftActionItems: event.draftActionItems,
        savedNotice: event.notice,
        errors: null,
      };
  }
}
