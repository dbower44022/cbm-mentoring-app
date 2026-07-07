/**
 * The templated-email compose dialog (WTK-169/179, REQ-076/077): the
 * staff-maintained template list, compose-from-template with the
 * engagement's merge fields resolved SERVER-SIDE, and the review-before-
 * send contract — the same POST prepares the preview (`confirmed: false`)
 * and performs the send (`confirmed: true`), so what the mentor read is
 * what goes out. Also exports the shared preview pane and the send-step
 * buttons the share-a-resource dialog composes with (one flow, two entry
 * points).
 */

import { type ReactElement, useEffect, useReducer, useState } from "react";

import { type ApiError, callApi, EnvelopeError } from "../api/envelope";
import { DeclinedNotice, UnreachableNotice } from "../shell/educate";
import { useEnvelope } from "../api/useEnvelope";
import { CHOOSING, type ComposePhase, reduceCompose } from "./compose-model";
import { type EmailSendPayload, type EmailTemplatePayload } from "./payloads";

export function refusalErrors(failure: unknown): ApiError[] | null {
  return failure instanceof EnvelopeError ? failure.errors : null;
}

// TData appears only in the return type by design (the envelope.ts pattern):
// the caller asserts the payload shape that fetch cannot know.
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters
export function postJson<TData>(path: string, body: unknown): Promise<{ data: TData }> {
  return callApi<TData>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function FailureNotice({ errors }: { errors: ApiError[] | null }): ReactElement {
  return errors === null ? <UnreachableNotice /> : <DeclinedNotice errors={errors} />;
}

/** The merged message, rendered for review — server copy verbatim. */
export function EmailPreviewPane({
  preview,
}: {
  preview: EmailSendPayload;
}): ReactElement {
  return (
    <div className="email-preview" aria-label="Email preview">
      <dl className="kv">
        <dt>To</dt>
        <dd>
          {preview.to.name} &lt;{preview.to.address}&gt;
        </dd>
        <dt>Subject</dt>
        <dd>{preview.subject}</dd>
      </dl>
      <pre className="email-body">{preview.body}</pre>
      <p className="preview-hint">
        Nothing has been sent — review the merged message, then Send.
      </p>
    </div>
  );
}

/**
 * The flow's shared body: preview → send → sent, driven by one `prepare`
 * seam (`confirmed` false = preview round trip, true = the send). The
 * hosting dialog owns the choose step and calls this for the rest.
 */
export function PreviewSendFlow({
  state,
  dispatch,
  prepare,
  onCompleted,
  onClose,
}: {
  state: ComposePhase;
  dispatch: (event: Parameters<typeof reduceCompose>[1]) => void;
  prepare: (confirmed: boolean) => Promise<EmailSendPayload>;
  onCompleted?: () => void;
  onClose: () => void;
}): ReactElement | null {
  const send = (): void => {
    dispatch({ kind: "sendRequested" });
    prepare(true)
      .then((result) => {
        dispatch({ kind: "sendSucceeded", result });
        onCompleted?.();
      })
      .catch((failure: unknown) => {
        dispatch({ kind: "refused", errors: refusalErrors(failure) });
      });
  };

  if (state.phase === "previewing") {
    return (
      <>
        <EmailPreviewPane preview={state.preview} />
        {state.errors !== null ? <FailureNotice errors={state.errors} /> : null}
        <div className="dialog-choices">
          <button type="button" disabled={state.busy} onClick={send}>
            Send
          </button>
          <button
            type="button"
            onClick={() => {
              dispatch({ kind: "backToChoosing" });
            }}
          >
            Back
          </button>
          <button type="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </>
    );
  }
  if (state.phase === "sent") {
    return (
      <>
        <p role="status">{state.result.confirmation}</p>
        <div className="dialog-choices">
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </>
    );
  }
  return null;
}

export interface ComposeEmailProps {
  engagementId: string;
  /** Preselect (and pin) one template — the accept flow's intro email. */
  fixedTemplateKey?: string;
  onClose: () => void;
}

export function ComposeEmailDialog({
  engagementId,
  fixedTemplateKey,
  onClose,
}: ComposeEmailProps): ReactElement {
  const { state: templatesState } =
    useEnvelope<EmailTemplatePayload[]>("/email/templates");
  const [templateKey, setTemplateKey] = useState<string>(fixedTemplateKey ?? "");
  const [flow, dispatch] = useReducer(reduceCompose, CHOOSING);

  // With a pinned template (the post-acceptance intro flow) the choose step
  // has nothing to choose — go straight to the preview round trip.
  useEffect(() => {
    if (fixedTemplateKey !== undefined) {
      requestPreview(fixedTemplateKey);
    }
    // Launch-once (empty deps): the pinned template is fixed for the
    // dialog's lifetime, so there is nothing to react to.
  }, []);

  const prepare = (key: string, confirmed: boolean): Promise<EmailSendPayload> =>
    postJson<EmailSendPayload>("/email/send", {
      templateKey: key,
      engagementID: engagementId,
      confirmed,
    }).then(({ data }) => data);

  function requestPreview(key: string): void {
    dispatch({ kind: "previewRequested" });
    prepare(key, false)
      .then((preview) => {
        dispatch({ kind: "previewArrived", preview });
      })
      .catch((failure: unknown) => {
        dispatch({ kind: "refused", errors: refusalErrors(failure) });
      });
  }

  return (
    <dialog open className="dialog" aria-label="Send templated email">
      <p className="educate-what">Send Email (templated)</p>
      {flow.phase === "choosing" ? (
        <>
          {templatesState.phase === "loading" ? (
            <p role="status">Loading templates…</p>
          ) : null}
          {templatesState.phase === "declined" ? (
            <DeclinedNotice errors={templatesState.errors} />
          ) : null}
          {templatesState.phase === "unreachable" ? <UnreachableNotice /> : null}
          {templatesState.phase === "loaded" ? (
            <>
              <label>
                Template{" "}
                <select
                  aria-label="Template"
                  value={templateKey}
                  disabled={fixedTemplateKey !== undefined}
                  onChange={(event) => {
                    setTemplateKey(event.target.value);
                  }}
                >
                  <option value="">Choose a template…</option>
                  {templatesState.data.map((template) => (
                    <option key={template.templateKey} value={template.templateKey}>
                      {template.templateName}
                    </option>
                  ))}
                </select>
              </label>
              <p className="preview-hint">
                The template merges this engagement's fields; you review the message
                before it sends. The list is staff-maintained.
              </p>
              {flow.errors !== null ? <FailureNotice errors={flow.errors} /> : null}
              <div className="dialog-choices">
                <button
                  type="button"
                  disabled={flow.busy || templateKey === ""}
                  onClick={() => {
                    requestPreview(templateKey);
                  }}
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
        prepare={(confirmed) => prepare(templateKey, confirmed)}
        onClose={onClose}
      />
    </dialog>
  );
}
