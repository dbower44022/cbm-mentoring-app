/**
 * Wire shapes for the workprocess surface (WTK-089/094/098, REQ-041/REQ-042),
 * following the grid/payloads.ts precedent: hand-maintained mirrors of the
 * Python router — `api/routers/workprocess.py` is the contract; this module
 * only names its camelCase payloads for the compiler.
 */

import { type ActionPayload } from "../grid/payloads";

/**
 * One action-list entry as `GET /workprocesses/actions/{dataSourceKey}`
 * serves it (REQ-041). The contract/classification vocabularies are derived
 * from the grid's ActionPayload, never redeclared: the server deliberately
 * speaks the grid standard's action vocabulary (the PanelAction wire shape)
 * so the menus render workprocesses like any declared action.
 */
export interface WorkprocessActionPayload {
  workprocessRegistrationID: string;
  label: string;
  description: string;
  selectionContract: ActionPayload["selectionContract"];
  classification: ActionPayload["classification"];
}

/** A run row as the router's `_run_payload` serializes it (REQ-042). */
export interface WorkprocessRunPayload {
  workprocessRunID: string;
  workprocessRegistrationID: string;
  runState: "inFlight" | "committed" | "discarded";
  selectedRecordIDs: string[];
  /** Pending answers only — nothing is applied until the run commits. */
  stepAnswers: Record<string, unknown>;
  /** Where the walk stands; null = a terminal step resolved. */
  currentStepKey: string | null;
  /** The frame's "you can commit now" fact, computed server-side. */
  completable: boolean;
  rowVersion: number;
}

/**
 * `POST /workprocesses/runs/{id}/commit` answers the run row plus the
 * success confirmation; `meta.affectedDataSourceKeys` names the grids the
 * workprocess targets.
 */
export interface WorkprocessCommitPayload extends WorkprocessRunPayload {
  confirmation: string;
}
