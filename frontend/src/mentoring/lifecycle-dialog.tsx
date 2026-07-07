/**
 * The engagement lifecycle flow (WTK-183, REQ-075): confirm → POST the
 * transition → confirmation, with the server's educate voice rendered
 * verbatim on refusal (an invalid-status invocation is a 422 that TEACHES,
 * per the never-hide rule — the action always ran). Every transition is
 * classified `modifying` and confirms here before anything moves (the
 * prototype's ruling: decline is a status change only per DEC-071, so it
 * confirms like the rest rather than taking the destructive path).
 *
 * Accepting surfaces the REQ-076 next steps IN PLACE: send the
 * introduction email (the compose dialog pinned to the intro template) and
 * schedule the first session (the inline schedule form).
 */

import { type ReactElement, useState } from "react";

import { type ApiError, callApi, EnvelopeError } from "../api/envelope";
import { type ActionPayload } from "../grid/payloads";
import { DeclinedNotice, UnreachableNotice } from "../shell/educate";
import { useEnvelope } from "../api/useEnvelope";
import { ComposeEmailDialog, refusalErrors } from "./compose-email";
import { type EngagementRollupPayload, type LifecycleResultPayload } from "./payloads";
import { ScheduleSessionForm } from "./schedule-session";

interface LifecyclePhase {
  applying: boolean;
  result: LifecycleResultPayload | null;
  errors: ApiError[] | null | "none";
}

export interface LifecycleDialogProps {
  action: ActionPayload;
  /** The server transition key (actions.lifecycleTransitionFor's answer). */
  transition: string;
  engagementId: string;
  /** Fires when the transition applied — the launching grid's refresh hook. */
  onCompleted: () => void;
  onClose: () => void;
}

export function LifecycleDialog({
  action,
  transition,
  engagementId,
  onCompleted,
  onClose,
}: LifecycleDialogProps): ReactElement {
  // The rollup read supplies what the confirm step must say truthfully —
  // the engagement's name and CURRENT status — plus the rowVersion the
  // write contract requires (DB-S4: every write carries the version read).
  const { state } = useEnvelope<EngagementRollupPayload>(
    `/engagements/${engagementId}/rollup`,
  );
  const [phase, setPhase] = useState<LifecyclePhase>({
    applying: false,
    result: null,
    errors: "none",
  });
  const [composeIntro, setComposeIntro] = useState<string | null>(null);
  const [scheduling, setScheduling] = useState(false);
  const [scheduledNotice, setScheduledNotice] = useState<string | null>(null);

  const apply = (rowVersion: number): void => {
    setPhase((current) => ({ ...current, applying: true, errors: "none" }));
    callApi<LifecycleResultPayload>(`/engagements/${engagementId}/lifecycle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transition, rowVersion }),
    })
      .then(({ data }) => {
        setPhase({ applying: false, result: data, errors: "none" });
        // The status flip is already real — the grid behind the dialog
        // refreshes now, not on Close.
        onCompleted();
      })
      .catch((failure: unknown) => {
        // A stale rowVersion is the standard 409; its message renders like
        // any refusal and Close → reopen rereads the fresh record.
        setPhase({
          applying: false,
          result: null,
          errors:
            failure instanceof EnvelopeError ? failure.errors : refusalErrors(failure),
        });
      });
  };

  return (
    <dialog open className="dialog" aria-label={action.label}>
      <p className="educate-what">{action.label}</p>

      {state.phase === "loading" ? <p role="status">Loading the engagement…</p> : null}
      {state.phase === "declined" ? <DeclinedNotice errors={state.errors} /> : null}
      {state.phase === "unreachable" ? <UnreachableNotice /> : null}

      {state.phase === "loaded" && phase.result === null ? (
        <>
          <p>
            {action.label} runs on{" "}
            <b>{state.data.engagement.engagementName ?? "this engagement"}</b>
            {state.data.engagement.engagementStatusLabel !== null ? (
              <>
                {" "}
                (status: <b>{state.data.engagement.engagementStatusLabel}</b>)
              </>
            ) : null}
            . Continue applies the status change; Cancel changes nothing.
          </p>
          {phase.errors !== "none" ? (
            phase.errors === null ? (
              <UnreachableNotice />
            ) : (
              <DeclinedNotice errors={phase.errors} />
            )
          ) : null}
          <div className="dialog-choices">
            <button
              type="button"
              disabled={phase.applying}
              onClick={() => {
                apply(state.data.engagement.rowVersion);
              }}
            >
              Continue
            </button>
            <button type="button" onClick={onClose}>
              Cancel
            </button>
          </div>
        </>
      ) : null}

      {phase.result !== null ? (
        <>
          <p role="status">{phase.result.confirmation}</p>
          {/* REQ-076: the accept flow's first steps, offered in place. */}
          {phase.result.nextSteps.length > 0 ? (
            <div className="next-steps" aria-label="Next steps">
              {phase.result.nextSteps.map((step) => (
                <button
                  key={step.key}
                  type="button"
                  onClick={() => {
                    if (step.key === "scheduleFirstSession") {
                      setScheduling(true);
                    } else if (step.templateKey !== undefined) {
                      setComposeIntro(step.templateKey);
                    }
                  }}
                >
                  {step.label}
                </button>
              ))}
            </div>
          ) : null}
          {scheduling ? (
            <ScheduleSessionForm
              engagementId={engagementId}
              onScheduled={() => {
                setScheduling(false);
                setScheduledNotice(
                  "Session scheduled — it appears in the engagement's session list and your Sessions area.",
                );
                onCompleted();
              }}
            />
          ) : null}
          {scheduledNotice !== null ? <p role="status">{scheduledNotice}</p> : null}
          <div className="dialog-choices">
            <button type="button" onClick={onClose}>
              Close
            </button>
          </div>
        </>
      ) : null}

      {composeIntro !== null ? (
        <ComposeEmailDialog
          engagementId={engagementId}
          fixedTemplateKey={composeIntro}
          onClose={() => {
            setComposeIntro(null);
          }}
        />
      ) : null}
    </dialog>
  );
}
