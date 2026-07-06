/**
 * Pure-logic tests for the workprocess run model (WTK-094/098, REQ-041/042):
 * the action-list mapping into the grid vocabulary, the inherited-selection
 * resolution, the launch→step→commit phase reducer (including stale events),
 * and the dirty-guard facts the cancel/close paths consume.
 */

import { describe, expect, it } from "vitest";

import {
  discardWarning,
  guardsUnload,
  LAUNCHING,
  launchSelection,
  reduceRun,
  type RunPhase,
  workprocessPanelAction,
} from "./model";
import { type WorkprocessActionPayload, type WorkprocessRunPayload } from "./payloads";

const ENTRY: WorkprocessActionPayload = {
  workprocessRegistrationID: "wp-reassign",
  label: "Reassign Mentor",
  description: "Moves the selected engagement to another mentor.",
  selectionContract: "single",
  classification: "modifying",
};

function makeRun(over: Partial<WorkprocessRunPayload> = {}): WorkprocessRunPayload {
  return {
    workprocessRunID: "run-1",
    workprocessRegistrationID: "wp-reassign",
    runState: "inFlight",
    selectedRecordIDs: ["r1"],
    stepAnswers: {},
    currentStepKey: "chooseMentor",
    completable: false,
    rowVersion: 1,
    ...over,
  };
}

describe("workprocessPanelAction (REQ-041)", () => {
  it("maps an action-list entry into the grid's action vocabulary, keyed by registration", () => {
    expect(workprocessPanelAction(ENTRY)).toEqual({
      key: "wp-reassign",
      label: "Reassign Mentor",
      selectionContract: "single",
      classification: "modifying",
    });
  });
});

describe("launchSelection (REQ-042: the run inherits the selection)", () => {
  const rows = [{ recordId: "r1" }, { recordId: "r2" }, { recordId: "r3" }];

  it("passes explicit record ids through", () => {
    expect(
      launchSelection({ kind: "explicit", recordIds: ["r2", "r9"] }, rows),
    ).toEqual(["r2", "r9"]);
    expect(launchSelection({ kind: "explicit", recordIds: [] }, rows)).toEqual([]);
  });

  it("resolves a filtered-set selection to the loaded rows — the client's only knowledge", () => {
    expect(launchSelection({ kind: "filteredSet" }, rows)).toEqual(["r1", "r2", "r3"]);
  });
});

describe("reduceRun: the launch→step→commit walk", () => {
  it("launching resolves to running on launch, or notLaunched on refusal", () => {
    const run = makeRun();
    expect(reduceRun(LAUNCHING, { kind: "launched", run })).toEqual({
      phase: "running",
      run,
      submitting: false,
      refusal: null,
    });
    const errors = [
      {
        fieldName: "selectedRecordIDs",
        code: "selectionContractViolation",
        message: "x",
      },
    ];
    expect(
      reduceRun(LAUNCHING, { kind: "launchRefused", refusal: { errors } }),
    ).toEqual({ phase: "notLaunched", refusal: { errors } });
  });

  it("a submit cycle advances the run and clears any earlier refusal", () => {
    const first = makeRun();
    let phase = reduceRun(LAUNCHING, { kind: "launched", run: first });
    phase = reduceRun(phase, {
      kind: "submitRefused",
      refusal: {
        errors: [{ fieldName: "stepKey", code: "notCurrentStep", message: "x" }],
      },
    });
    expect(phase).toMatchObject({ phase: "running", submitting: false });
    phase = reduceRun(phase, { kind: "submitStarted" });
    expect(phase).toMatchObject({ phase: "running", submitting: true, refusal: null });
    const advanced = makeRun({
      stepAnswers: { chooseMentor: "mentor-9" },
      currentStepKey: null,
      completable: true,
    });
    phase = reduceRun(phase, { kind: "advanced", run: advanced });
    expect(phase).toEqual({
      phase: "running",
      run: advanced,
      submitting: false,
      refusal: null,
    });
  });

  it("commit resolves to the terminal committed phase", () => {
    const running = reduceRun(LAUNCHING, { kind: "launched", run: makeRun() });
    const committed = makeRun({ runState: "committed", currentStepKey: null });
    expect(
      reduceRun(running, { kind: "committed", run: committed, confirmation: "Done." }),
    ).toEqual({ phase: "committed", run: committed, confirmation: "Done." });
  });

  it("ignores events that don't belong to the current phase (stale responses)", () => {
    // A step answer landing before launch resolves must not invent a run.
    expect(reduceRun(LAUNCHING, { kind: "advanced", run: makeRun() })).toBe(LAUNCHING);
    // Nothing moves a terminal phase but closing the surface.
    const committed: RunPhase = {
      phase: "committed",
      run: makeRun({ runState: "committed" }),
      confirmation: "Done.",
    };
    expect(reduceRun(committed, { kind: "launched", run: makeRun() })).toBe(committed);
    const notLaunched: RunPhase = { phase: "notLaunched", refusal: { errors: null } };
    expect(reduceRun(notLaunched, { kind: "submitStarted" })).toBe(notLaunched);
  });
});

describe("the dirty guard (REQ-042: leaving/cancelling = discard, after a warning)", () => {
  it("guards only while a run is open", () => {
    expect(discardWarning(LAUNCHING, "Reassign Mentor")).toBeNull();
    expect(guardsUnload(LAUNCHING)).toBe(false);

    const running = reduceRun(LAUNCHING, { kind: "launched", run: makeRun() });
    expect(discardWarning(running, "Reassign Mentor")).not.toBeNull();
    expect(guardsUnload(running)).toBe(true);

    const committed = reduceRun(running, {
      kind: "committed",
      run: makeRun({ runState: "committed" }),
      confirmation: "Done.",
    });
    expect(discardWarning(committed, "Reassign Mentor")).toBeNull();
    expect(guardsUnload(committed)).toBe(false);
  });

  it("says nothing was applied and counts the answers leaving would discard", () => {
    const noAnswers = reduceRun(LAUNCHING, { kind: "launched", run: makeRun() });
    expect(discardWarning(noAnswers, "Reassign Mentor")).toEqual({
      title: "Leave 'Reassign Mentor' without finishing?",
      detail:
        "Nothing has been applied — a workprocess only takes effect when it " +
        "completes. Leaving discards the run.",
    });

    const oneAnswer = reduceRun(LAUNCHING, {
      kind: "launched",
      run: makeRun({ stepAnswers: { chooseMentor: "mentor-9" } }),
    });
    expect(discardWarning(oneAnswer, "Reassign Mentor")?.detail).toContain(
      "Leaving discards the run and the 1 answer recorded so far.",
    );

    const twoAnswers = reduceRun(LAUNCHING, {
      kind: "launched",
      run: makeRun({ stepAnswers: { chooseMentor: "mentor-9", confirm: true } }),
    });
    expect(discardWarning(twoAnswers, "Reassign Mentor")?.detail).toContain(
      "Leaving discards the run and the 2 answers recorded so far.",
    );
  });
});
