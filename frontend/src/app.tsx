/**
 * Scaffold root (WTK-194). Proves the rendering contract end to end: fetch a
 * server view-model through the envelope client and render what the server
 * said — including its educate-voice error messages — verbatim. The Python
 * view-models in src/mentorapp/ui/ are the single source of behavior; nothing
 * here re-derives navigation, permissions, or messaging. PI-011's follow-on
 * tasks replace this boot screen with the real shell rendering.
 */

import { type ReactElement, useEffect, useState } from "react";
import { type ApiError, callApi, EnvelopeError } from "./api/envelope";

type BootState =
  | { phase: "loading" }
  | { phase: "connected" }
  | { phase: "declined"; errors: ApiError[] }
  | { phase: "unreachable" };

export function App(): ReactElement {
  const [state, setState] = useState<BootState>({ phase: "loading" });

  useEffect(() => {
    let cancelled = false;
    const update = (next: BootState): void => {
      if (!cancelled) {
        setState(next);
      }
    };
    void callApi<unknown>("/shell")
      .then(() => {
        update({ phase: "connected" });
      })
      .catch((failure: unknown) => {
        // A structured decline (e.g. unauthenticated) proves the contract just
        // as well as a success: the server's own messages render untouched.
        if (failure instanceof EnvelopeError) {
          update({ phase: "declined", errors: failure.errors });
        } else {
          update({ phase: "unreachable" });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  switch (state.phase) {
    case "loading":
      return <p>Reaching the CBM Mentoring API…</p>;
    case "connected":
      return <p>Connected: the shell view-model answered.</p>;
    case "declined":
      return (
        <div>
          <p>The shell view-model declined this request:</p>
          <ul>
            {state.errors.map((error) => (
              <li key={`${error.code}:${error.fieldName ?? ""}`}>{error.message}</li>
            ))}
          </ul>
        </div>
      );
    case "unreachable":
      return (
        <p>
          The API is not reachable. Start it with{" "}
          <code>uv run uvicorn mentorapp.main:app --reload</code> and reload this page.
        </p>
      );
  }
}
