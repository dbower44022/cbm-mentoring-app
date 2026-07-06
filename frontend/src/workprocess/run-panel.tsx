/**
 * The workprocess run surface (WTK-094/098, REQ-042): launch → step → commit
 * against the /workprocesses run verbs. Hosted as an overlay dialog INSIDE
 * the launching grid panel, not a separate window/route: the launch inherits
 * the grid's live selection and completion refreshes that same grid through
 * a direct callback, so no cross-window broadcast is needed — the app's
 * real-window pattern (windows/record.tsx) exists for pinned reference
 * windows, and a run is a transactional frame of its launching panel.
 *
 * The frame renders only what the wire serves: the run row's current step
 * key and pending-answer count. The registration's stepGraph (where an
 * author COULD declare richer step content) is an admin-gated read the
 * running user never sees, so a step renders as its author's key verbatim
 * plus one free-form answer control stored verbatim as the step's JSON
 * answer — the frame never invents per-step semantics (author freedom).
 * Branch routing is server-side: the step's declared successor decides;
 * this surface never sends a nextStepKey override.
 */

import { type ReactElement, useEffect, useReducer, useRef, useState } from "react";

import { type ApiError, callApi, EnvelopeError } from "../api/envelope";
import { DeclinedNotice, UnreachableNotice } from "../shell/educate";
import {
  discardWarning,
  guardsUnload,
  LAUNCHING,
  reduceRun,
  type RunRefusal,
} from "./model";
import {
  type WorkprocessActionPayload,
  type WorkprocessCommitPayload,
  type WorkprocessRunPayload,
} from "./payloads";

function refusalFrom(failure: unknown): RunRefusal {
  return { errors: failure instanceof EnvelopeError ? failure.errors : null };
}

/** The server's own words for a refusal; unreachable is the one local copy. */
function FailureNotice({ errors }: { errors: ApiError[] | null }): ReactElement {
  return errors === null ? <UnreachableNotice /> : <DeclinedNotice errors={errors} />;
}

// TData appears only in the return type by design (the envelope.ts pattern):
// the caller asserts the payload shape that fetch cannot know.
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters
function postJson<TData>(path: string, body: unknown): Promise<{ data: TData }> {
  return callApi<TData>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export interface WorkprocessRunProps {
  entry: WorkprocessActionPayload;
  dataSourceKey: string;
  selectedRecordIds: readonly string[];
  /** Fires once, on successful commit — the launching grid's refresh hook. */
  onCompleted: () => void;
  /** Fires whenever the surface is done (committed, discarded, or refused). */
  onClose: () => void;
}

export function WorkprocessRunPanel({
  entry,
  dataSourceKey,
  selectedRecordIds,
  onCompleted,
  onClose,
}: WorkprocessRunProps): ReactElement {
  const [phase, dispatch] = useReducer(reduceRun, LAUNCHING);
  const [answerText, setAnswerText] = useState("");
  const [confirmingLeave, setConfirmingLeave] = useState(false);
  const [helpNotice, setHelpNotice] = useState(false);
  const launched = useRef(false);

  // Launch exactly once per surface. POST /workprocesses/runs is not
  // idempotent, and StrictMode's dev-mode double effect (or any re-render)
  // must not open a second run — the ref, not the dep list, is the guard.
  useEffect(() => {
    if (launched.current) {
      return;
    }
    launched.current = true;
    postJson<WorkprocessRunPayload>("/workprocesses/runs", {
      workprocessRegistrationID: entry.workprocessRegistrationID,
      dataSourceKey,
      selectedRecordIDs: selectedRecordIds,
    })
      .then(({ data }) => {
        dispatch({ kind: "launched", run: data });
      })
      .catch((failure: unknown) => {
        dispatch({ kind: "launchRefused", refusal: refusalFrom(failure) });
      });
    // The ref makes re-runs no-ops, so the effect deliberately has no deps.
  }, []);

  // The browser-native prompt is the ONE leave path the in-UI guard can't
  // intercept (tab/window close). Abandonment is still safe — nothing applies
  // without an explicit commit — but the user must knowingly choose it.
  useEffect(() => {
    if (!guardsUnload(phase)) {
      return;
    }
    const warn = (event: BeforeUnloadEvent): void => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
    };
  }, [phase]);

  const warning = discardWarning(phase, entry.label);

  const submitAnswer = (): void => {
    if (phase.phase !== "running" || phase.submitting) {
      return;
    }
    const stepKey = phase.run.currentStepKey;
    if (stepKey === null) {
      return;
    }
    dispatch({ kind: "submitStarted" });
    // The answer is the author's document — sent verbatim as a string; the
    // engine stores it uninterpreted and the author's handler reads it.
    postJson<WorkprocessRunPayload>(
      `/workprocesses/runs/${phase.run.workprocessRunID}/step`,
      { stepKey, answer: answerText },
    )
      .then(({ data }) => {
        setAnswerText("");
        dispatch({ kind: "advanced", run: data });
      })
      .catch((failure: unknown) => {
        dispatch({ kind: "submitRefused", refusal: refusalFrom(failure) });
      });
  };

  const completeRun = (): void => {
    if (phase.phase !== "running" || phase.submitting) {
      return;
    }
    dispatch({ kind: "submitStarted" });
    // Commit (like cancel) declares no request body — the run id says it all.
    callApi<WorkprocessCommitPayload>(
      `/workprocesses/runs/${phase.run.workprocessRunID}/commit`,
      { method: "POST" },
    )
      .then(({ data }) => {
        dispatch({ kind: "committed", run: data, confirmation: data.confirmation });
        // Refresh immediately on commit, not on Close: the applied changes
        // are already real, and the grid behind the dialog must say so.
        onCompleted();
      })
      .catch((failure: unknown) => {
        dispatch({ kind: "submitRefused", refusal: refusalFrom(failure) });
      });
  };

  const requestClose = (): void => {
    // Leaving = cancel, but only after the dirty guard while a run is open
    // (REQ-042); the other phases have nothing pending and close directly.
    if (warning !== null) {
      setConfirmingLeave(true);
      return;
    }
    onClose();
  };

  const discardRun = (): void => {
    const runId = phase.phase === "running" ? phase.run.workprocessRunID : null;
    if (runId === null) {
      onClose();
      return;
    }
    // Close whether or not the cancel call lands: nothing can ever apply
    // without an explicit commit, so exit is always safe — a failed cancel
    // just leaves the run row in flight server-side instead of `discarded`.
    void callApi(`/workprocesses/runs/${runId}/cancel`, { method: "POST" })
      .catch(() => undefined)
      .finally(onClose);
  };

  return (
    <dialog open className="dialog" aria-label={`Run '${entry.label}'`}>
      <p className="educate-what">{entry.label}</p>
      <p>{entry.description}</p>

      {phase.phase === "launching" ? <p role="status">Starting the run…</p> : null}

      {phase.phase === "notLaunched" ? (
        <>
          <FailureNotice errors={phase.refusal.errors} />
          <div className="dialog-choices">
            <button type="button" onClick={onClose}>
              Close
            </button>
          </div>
        </>
      ) : null}

      {phase.phase === "running" && phase.run.currentStepKey !== null ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            submitAnswer();
          }}
        >
          {/* The author's step key IS the title the wire serves — rendering
              it verbatim keeps the frame from inventing step semantics. */}
          <h2>{phase.run.currentStepKey}</h2>
          <label>
            Answer{" "}
            <input
              value={answerText}
              autoFocus
              aria-label={`Answer for step '${phase.run.currentStepKey}'`}
              onChange={(event) => {
                setAnswerText(event.target.value);
              }}
            />
          </label>
          {phase.refusal !== null ? (
            <FailureNotice errors={phase.refusal.errors} />
          ) : null}
          <div className="dialog-choices">
            <button type="submit">Continue</button>
            <button
              type="button"
              onClick={() => {
                setHelpNotice(true);
              }}
            >
              Help
            </button>
            <button type="button" onClick={requestClose}>
              Cancel run
            </button>
          </div>
        </form>
      ) : null}

      {phase.phase === "running" && phase.run.currentStepKey === null ? (
        <>
          <p role="status">
            Every step is answered. Nothing has been applied yet — completing &apos;
            {entry.label}&apos; applies its changes.
          </p>
          {phase.refusal !== null ? (
            <FailureNotice errors={phase.refusal.errors} />
          ) : null}
          <div className="dialog-choices">
            <button type="button" onClick={completeRun}>
              Complete
            </button>
            <button type="button" onClick={requestClose}>
              Cancel run
            </button>
          </div>
        </>
      ) : null}

      {phase.phase === "committed" ? (
        <>
          <p role="status">{phase.confirmation}</p>
          <div className="dialog-choices">
            <button type="button" onClick={onClose}>
              Close
            </button>
          </div>
        </>
      ) : null}

      {/* Per-step Help: the shell's interim help mechanism and voice
          (shell.tsx onMenuAction) — a dismissible notice, never a dead
          control, until the admin-configured help mapping ships. */}
      {helpNotice ? (
        <p className="notice" role="status">
          No step-specific help exists yet for this workprocess. The help mapping is
          configured by administrators and hasn&apos;t shipped.{" "}
          <button
            type="button"
            onClick={() => {
              setHelpNotice(false);
            }}
          >
            Dismiss
          </button>
        </p>
      ) : null}

      {confirmingLeave && warning !== null ? (
        <dialog open className="dialog" aria-label={warning.title}>
          <p className="educate-what">{warning.title}</p>
          <p>{warning.detail}</p>
          <div className="dialog-choices">
            <button
              type="button"
              onClick={() => {
                setConfirmingLeave(false);
              }}
            >
              Keep working
            </button>
            <button type="button" onClick={discardRun}>
              Discard the run
            </button>
          </div>
        </dialog>
      ) : null}
    </dialog>
  );
}
