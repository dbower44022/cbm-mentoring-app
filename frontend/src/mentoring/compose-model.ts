/**
 * Pure state for the compose-from-template flow (WTK-169/179, REQ-077):
 * choose → preview (the server's merged message, REVIEW BEFORE SEND) →
 * send → sent. One reducer serves both the engagement compose dialog and
 * the share-a-resource dialog — they differ only in what the choose step
 * selects and which endpoint prepares the message, never in the flow.
 */

import { type ApiError } from "../api/envelope";
import { type EmailSendPayload } from "./payloads";

export type ComposePhase =
  | { phase: "choosing"; busy: boolean; errors: ApiError[] | null }
  | {
      phase: "previewing";
      preview: EmailSendPayload;
      busy: boolean;
      errors: ApiError[] | null;
    }
  | { phase: "sent"; result: EmailSendPayload };

export const CHOOSING: ComposePhase = { phase: "choosing", busy: false, errors: null };

export type ComposeEvent =
  | { kind: "previewRequested" }
  | { kind: "previewArrived"; preview: EmailSendPayload }
  | { kind: "sendRequested" }
  | { kind: "sendSucceeded"; result: EmailSendPayload }
  | { kind: "refused"; errors: ApiError[] | null }
  | { kind: "backToChoosing" };

export function reduceCompose(state: ComposePhase, event: ComposeEvent): ComposePhase {
  switch (event.kind) {
    case "previewRequested":
      return state.phase === "choosing"
        ? { ...state, busy: true, errors: null }
        : state;
    case "previewArrived":
      // The preview is the review moment (REQ-077) — nothing has been sent;
      // the payload itself says so.
      return { phase: "previewing", preview: event.preview, busy: false, errors: null };
    case "sendRequested":
      return state.phase === "previewing"
        ? { ...state, busy: true, errors: null }
        : state;
    case "sendSucceeded":
      return { phase: "sent", result: event.result };
    case "refused":
      // Refusals land on the phase they interrupted, in the server's words.
      if (state.phase === "previewing") {
        return { ...state, busy: false, errors: event.errors ?? [] };
      }
      return { phase: "choosing", busy: false, errors: event.errors ?? [] };
    case "backToChoosing":
      return CHOOSING;
  }
}
