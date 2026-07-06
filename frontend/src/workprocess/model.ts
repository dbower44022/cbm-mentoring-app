/**
 * Workprocess run model (WTK-094/098, REQ-042): the pure logic behind the
 * run surface — mapping action-list entries into the grid's action
 * vocabulary, the launch→step→commit phase reducer, and the dirty-guard
 * facts. The server owns every behavioral decision (branching, contract
 * checks, what an answer means); this module only tracks which phase the
 * surface is in and what leaving it would discard.
 */

import { type ApiError } from "../api/envelope";
import { type ActionPayload } from "../grid/payloads";
import { type Selection } from "../grid/model";
import { type WorkprocessActionPayload, type WorkprocessRunPayload } from "./payloads";

// --- Action-list mapping (REQ-041) -------------------------------------------

/**
 * An action-list entry in the grid menus' vocabulary. The registration id is
 * the action key: it is the launch identity and can never collide with a
 * grid's own declared action keys (those are short names, not UUIDs).
 */
export function workprocessPanelAction(entry: WorkprocessActionPayload): ActionPayload {
  return {
    key: entry.workprocessRegistrationID,
    label: entry.label,
    selectionContract: entry.selectionContract,
    classification: entry.classification,
  };
}

/**
 * The record ids a launch inherits from the grid's selection (REQ-042).
 * A filtered-set selection resolves to the loaded rows — the client's only
 * knowledge of the set — mirroring the destructive-confirmation precedent in
 * grid-panel.tsx (the same honest reading without the full id list).
 */
export function launchSelection(
  selection: Selection,
  loadedRows: readonly { recordId: string }[],
): string[] {
  if (selection.kind === "filteredSet") {
    return loadedRows.map((row) => row.recordId);
  }
  return [...selection.recordIds];
}

// --- The run phase reducer (REQ-042) ------------------------------------------

/**
 * One failed request as the surface renders it: `errors: null` means no
 * envelope came back at all (the unreachable rendering); a non-null list is
 * the server's own educate-voice refusal, shown verbatim.
 */
export interface RunRefusal {
  errors: ApiError[] | null;
}

/** The four phases the run surface can be in. */
export type RunPhase =
  | { phase: "launching" }
  | {
      phase: "running";
      run: WorkprocessRunPayload;
      submitting: boolean;
      refusal: RunRefusal | null;
    }
  | { phase: "committed"; run: WorkprocessRunPayload; confirmation: string }
  | { phase: "notLaunched"; refusal: RunRefusal };

export type RunEvent =
  | { kind: "launched"; run: WorkprocessRunPayload }
  | { kind: "launchRefused"; refusal: RunRefusal }
  | { kind: "submitStarted" }
  | { kind: "advanced"; run: WorkprocessRunPayload }
  | { kind: "submitRefused"; refusal: RunRefusal }
  | { kind: "committed"; run: WorkprocessRunPayload; confirmation: string };

export const LAUNCHING: RunPhase = { phase: "launching" };

/**
 * Advance the surface's phase. Events that don't belong to the current phase
 * (e.g. a stale response landing after the run already committed) leave the
 * phase unchanged — a late network answer must never corrupt the surface.
 */
export function reduceRun(phase: RunPhase, event: RunEvent): RunPhase {
  if (phase.phase === "launching") {
    if (event.kind === "launched") {
      return { phase: "running", run: event.run, submitting: false, refusal: null };
    }
    if (event.kind === "launchRefused") {
      return { phase: "notLaunched", refusal: event.refusal };
    }
    return phase;
  }
  if (phase.phase === "running") {
    switch (event.kind) {
      case "submitStarted":
        return { ...phase, submitting: true, refusal: null };
      case "advanced":
        return { phase: "running", run: event.run, submitting: false, refusal: null };
      case "submitRefused":
        return { ...phase, submitting: false, refusal: event.refusal };
      case "committed":
        return { phase: "committed", run: event.run, confirmation: event.confirmation };
      default:
        return phase;
    }
  }
  // committed / notLaunched are terminal: only closing the surface leaves them.
  return phase;
}

// --- Dirty guard (REQ-042: leaving/cancelling = discard, after a warning) -----

export interface DiscardWarning {
  title: string;
  /** Educate voice: why leaving is safe to allow, and what it costs. */
  detail: string;
}

/**
 * Non-null while leaving the surface would discard an open run. Only the
 * running phase guards: before launch there is nothing to discard, and the
 * committed/notLaunched phases have nothing pending by definition.
 */
export function discardWarning(
  phase: RunPhase,
  workprocessLabel: string,
): DiscardWarning | null {
  if (phase.phase !== "running") {
    return null;
  }
  const answered = Object.keys(phase.run.stepAnswers).length;
  const cost =
    answered === 0
      ? "Leaving discards the run."
      : `Leaving discards the run and the ${String(answered)} answer${
          answered === 1 ? "" : "s"
        } recorded so far.`;
  return {
    title: `Leave '${workprocessLabel}' without finishing?`,
    detail: `Nothing has been applied — a workprocess only takes effect when it completes. ${cost}`,
  };
}

/** Whether closing the browser window mid-run needs the native prompt. */
export function guardsUnload(phase: RunPhase): boolean {
  return phase.phase === "running";
}
