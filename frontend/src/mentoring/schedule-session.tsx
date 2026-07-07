/**
 * The schedule-a-session form (WTK-168, REQ-078's manual core): date/time
 * plus an optional pasted conference link (REQ-079; REQ-080's auto-created
 * org meeting lands on the same column later). Inline, not a route — it is
 * a step of two flows: the accept-assignment next steps (REQ-076) and the
 * prep surface's "schedule the next session while concluding" affordance.
 */

import { type ReactElement, useState } from "react";

import { type ApiError } from "../api/envelope";
import { DeclinedNotice, UnreachableNotice } from "../shell/educate";
import { postJson, refusalErrors } from "./compose-email";
import { type SessionRecordPayload } from "./payloads";

export interface ScheduleSessionProps {
  engagementId: string;
  onScheduled: (created: SessionRecordPayload) => void;
}

export function ScheduleSessionForm({
  engagementId,
  onScheduled,
}: ScheduleSessionProps): ReactElement {
  const [scheduledAt, setScheduledAt] = useState("");
  const [conferenceLink, setConferenceLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<ApiError[] | null | "none">("none");

  const submit = (): void => {
    if (scheduledAt === "" || busy) {
      return;
    }
    setBusy(true);
    setErrors("none");
    postJson<SessionRecordPayload>(`/engagements/${engagementId}/sessions`, {
      // datetime-local yields a zoneless stamp; it is the mentor's wall
      // clock, so it travels with the browser's own offset made explicit.
      scheduledAt: new Date(scheduledAt).toISOString(),
      conferenceLink: conferenceLink === "" ? null : conferenceLink,
    })
      .then(({ data }) => {
        setBusy(false);
        onScheduled(data);
      })
      .catch((failure: unknown) => {
        setBusy(false);
        setErrors(refusalErrors(failure));
      });
  };

  return (
    <form
      className="schedule-session"
      aria-label="Schedule a session"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <label>
        Date &amp; time{" "}
        <input
          type="datetime-local"
          aria-label="Session date and time"
          value={scheduledAt}
          onChange={(event) => {
            setScheduledAt(event.target.value);
          }}
        />
      </label>
      <label>
        Conference link{" "}
        <input
          type="url"
          aria-label="Conference link"
          placeholder="Paste a meeting link (optional)"
          value={conferenceLink}
          onChange={(event) => {
            setConferenceLink(event.target.value);
          }}
        />
      </label>
      {errors !== "none" ? (
        errors === null ? (
          <UnreachableNotice />
        ) : (
          <DeclinedNotice errors={errors} />
        )
      ) : null}
      <button type="submit" disabled={busy || scheduledAt === ""}>
        Schedule Session
      </button>
    </form>
  );
}
