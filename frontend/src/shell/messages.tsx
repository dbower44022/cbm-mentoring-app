/**
 * The one rendering of an admin message (REQ-011), shared by the Home
 * messages dashlet and the expanded urgent banner. Owns the acknowledgment
 * affordance: acknowledgment only ever happens by this explicit click —
 * no view, banner, or read implies it — and the affordance simply does not
 * render for a message that never asked (has_acknowledged is quiet state,
 * not a hidden action: there is nothing to invoke).
 */

import { type ReactElement, useState } from "react";

import { type ApiError, callApi, EnvelopeError } from "../api/envelope";
import { type AdminMessagePayload } from "../api/payloads";
import { DeclinedNotice } from "./educate";

export function formatPostedLine(message: AdminMessagePayload): string {
  // postedAt is an ISO instant; the locale rendering is display-only.
  return `Posted by ${message.postedBy} on ${new Date(message.postedAt).toLocaleString()}`;
}

export function AdminMessageView({
  message,
  onAcknowledged,
}: {
  message: AdminMessagePayload;
  onAcknowledged: (messageKey: string) => void;
}): ReactElement {
  const [errors, setErrors] = useState<ApiError[] | null>(null);

  const acknowledge = (): void => {
    void callApi<{ messageKey: string; acknowledged: boolean }>(
      `/home/messages/${message.messageKey}/acknowledge`,
      { method: "POST" },
    )
      .then(() => {
        setErrors(null);
        onAcknowledged(message.messageKey);
      })
      .catch((failure: unknown) => {
        // A decline renders in place (educate, never silent); anything else
        // gets the one client-owned copy so the click never just vanishes.
        setErrors(
          failure instanceof EnvelopeError
            ? failure.errors
            : [
                {
                  fieldName: null,
                  code: "unreachable",
                  message:
                    "Your acknowledgment couldn't be recorded — the API did " +
                    "not answer. Try again in a moment.",
                },
              ],
        );
      });
  };

  return (
    <article aria-label={message.title}>
      <h3>{message.title}</h3>
      <p>{formatPostedLine(message)}</p>
      <p>{message.body}</p>
      {message.requiresAcknowledgment &&
        (message.acknowledged ? (
          <p>Acknowledged.</p>
        ) : (
          <button type="button" onClick={acknowledge}>
            Acknowledge
          </button>
        ))}
      {errors !== null && <DeclinedNotice errors={errors} />}
    </article>
  );
}
