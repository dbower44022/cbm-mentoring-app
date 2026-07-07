/**
 * Share-a-resource (WTK-178, REQ-084): the templated email carrying the
 * resource LINK — the library holds references, never attachments. The
 * choose step picks WHICH engagement's contact receives it (the scoped
 * `GET /engagements` picker read); the rest is the one compose flow
 * (preview-before-send) against `POST /resources/{id}/share`, whose
 * template is fixed server-side to `resourceShare`.
 */

import { type ReactElement, useReducer, useState } from "react";

import { useEnvelope } from "../api/useEnvelope";
import { DeclinedNotice, UnreachableNotice } from "../shell/educate";
import { CHOOSING, reduceCompose } from "./compose-model";
import { postJson, PreviewSendFlow, refusalErrors } from "./compose-email";
import { type EmailSendPayload, type EngagementPayload } from "./payloads";

export interface ShareResourceProps {
  resourceId: string;
  onClose: () => void;
}

export function ShareResourceDialog({
  resourceId,
  onClose,
}: ShareResourceProps): ReactElement {
  const { state: engagements } = useEnvelope<EngagementPayload[]>("/engagements");
  const [engagementId, setEngagementId] = useState("");
  const [flow, dispatch] = useReducer(reduceCompose, CHOOSING);

  const prepare = (confirmed: boolean): Promise<EmailSendPayload> =>
    postJson<EmailSendPayload>(`/resources/${resourceId}/share`, {
      engagementID: engagementId,
      confirmed,
    }).then(({ data }) => data);

  const requestPreview = (): void => {
    dispatch({ kind: "previewRequested" });
    prepare(false)
      .then((preview) => {
        dispatch({ kind: "previewArrived", preview });
      })
      .catch((failure: unknown) => {
        dispatch({ kind: "refused", errors: refusalErrors(failure) });
      });
  };

  return (
    <dialog open className="dialog" aria-label="Share this resource">
      <p className="educate-what">Share with a Client</p>
      {flow.phase === "choosing" ? (
        <>
          {engagements.phase === "loading" ? (
            <p role="status">Loading engagements…</p>
          ) : null}
          {engagements.phase === "declined" ? (
            <DeclinedNotice errors={engagements.errors} />
          ) : null}
          {engagements.phase === "unreachable" ? <UnreachableNotice /> : null}
          {engagements.phase === "loaded" ? (
            <>
              <label>
                Engagement{" "}
                <select
                  aria-label="Engagement"
                  value={engagementId}
                  onChange={(event) => {
                    setEngagementId(event.target.value);
                  }}
                >
                  <option value="">Choose an engagement…</option>
                  {engagements.data.map((engagement) => (
                    <option
                      key={engagement.engagementID}
                      value={engagement.engagementID}
                    >
                      {engagement.engagementName ?? engagement.engagementID}
                      {engagement.primaryContactName !== null
                        ? ` — ${engagement.primaryContactName}`
                        : ""}
                    </option>
                  ))}
                </select>
              </label>
              <p className="preview-hint">
                The resource share email carries the resource's link, merged for this
                engagement's contact; you review it before it sends.
              </p>
              {flow.errors !== null ? <DeclinedNotice errors={flow.errors} /> : null}
              <div className="dialog-choices">
                <button
                  type="button"
                  disabled={flow.busy || engagementId === ""}
                  onClick={requestPreview}
                >
                  Preview
                </button>
                <button type="button" onClick={onClose}>
                  Cancel
                </button>
              </div>
            </>
          ) : null}
        </>
      ) : null}
      <PreviewSendFlow
        state={flow}
        dispatch={dispatch}
        prepare={prepare}
        onClose={onClose}
      />
    </dialog>
  );
}
