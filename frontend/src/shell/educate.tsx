/**
 * The one rendering of the app-wide educate-voice shapes: an
 * `EducatePayload` (what happened → why → what next) and the two failure
 * states every `useEnvelope` surface shares. Server copy renders verbatim —
 * these components never reword a message, only lay it out.
 */

import { type ReactElement } from "react";

import { type ApiError } from "../api/envelope";
import { type EducatePayload } from "../api/payloads";

export function EducateNotice({ notice }: { notice: EducatePayload }): ReactElement {
  return (
    <div role="note">
      <p>{notice.whatHappened}</p>
      <p>{notice.why}</p>
      <p>{notice.whatNext}</p>
    </div>
  );
}

/** A structured decline: the server's own messages, untouched. */
export function DeclinedNotice({ errors }: { errors: ApiError[] }): ReactElement {
  return (
    <div role="alert">
      <ul>
        {errors.map((error) => (
          <li key={`${error.code}:${error.fieldName ?? ""}`}>{error.message}</li>
        ))}
      </ul>
    </div>
  );
}

/** No envelope came back at all — the only copy the client itself owns. */
export function UnreachableNotice(): ReactElement {
  return (
    <EducateNotice
      notice={{
        whatHappened: "This couldn't be loaded.",
        why: "The CBM Mentoring API did not answer.",
        whatNext: "Check your connection and try again in a moment.",
      }}
    />
  );
}
