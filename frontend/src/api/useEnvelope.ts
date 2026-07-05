/**
 * The one GET-and-render seam every panel and window uses: fetch a server
 * view-model through the envelope client and expose the four states a
 * surface can be in. Inputs: an API path plus an optional refresh token
 * (bump it to re-fetch — e.g. the urgent banner after Home's view read its
 * messages). Output: the current state and a manual `reload`. Failure modes
 * mirror the scaffold's boot screen: a structured decline carries the
 * server's own messages; anything else is "unreachable".
 */

import { useCallback, useEffect, useState } from "react";

import { type ApiError, callApi, EnvelopeError } from "./envelope";

export type EnvelopeState<TData> =
  | { phase: "loading" }
  | { phase: "loaded"; data: TData; meta: Record<string, unknown> }
  | { phase: "declined"; errors: ApiError[] }
  | { phase: "unreachable" };

export function useEnvelope<TData>(
  path: string,
  refreshToken: unknown = null,
): { state: EnvelopeState<TData>; reload: () => void } {
  const [state, setState] = useState<EnvelopeState<TData>>({ phase: "loading" });
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState({ phase: "loading" });
    void callApi<TData>(path)
      .then((result) => {
        if (!cancelled) {
          setState({ phase: "loaded", data: result.data, meta: result.meta });
        }
      })
      .catch((failure: unknown) => {
        if (cancelled) {
          return;
        }
        if (failure instanceof EnvelopeError) {
          setState({ phase: "declined", errors: failure.errors });
        } else {
          setState({ phase: "unreachable" });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [path, generation, refreshToken]);

  const reload = useCallback(() => {
    setGeneration((current) => current + 1);
  }, []);

  return { state, reload };
}
