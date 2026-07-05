/**
 * The typed client for the one response shape every mentorapp endpoint speaks
 * (mirrors src/mentorapp/api/envelope.py — that module is the contract; this
 * one only transports it), and the ONE interception point for session
 * expiry (DEC-080): a `reauthRequired` refusal hands off to the registered
 * window session controller instead of surfacing to the caller.
 *
 * Inputs: an API path (same-origin; the Vite dev server proxies API prefixes
 * to FastAPI) and optional fetch init. Output: the unwrapped `{data, meta}` of
 * a success envelope. Failure modes: `EnvelopeError` whenever `errors` is
 * non-null — carrying every structured entry (a multi-field save reports ALL
 * failures in one round trip) plus the failure `data` body some recoveries
 * need (current record on a 409 conflict, duplicate candidates on create).
 */

import { readSession } from "./session";

export interface ApiError {
  // Wire names are the contract — camelCase per the data-model standard.
  fieldName: string | null;
  code: string;
  message: string;
}

export interface Envelope<TData> {
  data: TData;
  meta: Record<string, unknown>;
  errors: ApiError[] | null;
}

export class EnvelopeError extends Error {
  readonly status: number;
  readonly errors: ApiError[];
  /** Recovery body served alongside the failure (409 conflict/duplicates). */
  readonly data: unknown;

  constructor(status: number, errors: ApiError[], data: unknown) {
    super(errors.map((e) => e.message).join("; "));
    this.name = "EnvelopeError";
    this.status = status;
    this.errors = errors;
    this.data = data;
  }
}

/** The refusal code `mentorapp.api.errors` serves for an expired session. */
export const CODE_REAUTH_REQUIRED = "reauthRequired";

/**
 * Revive the expired session — overlay REAUTH_SCREEN in place, run
 * `POST /auth/reauth`, store the rotated reference — and resolve true once
 * revived (false when the session is beyond revival, e.g. ENDED).
 */
export type ReauthHandler = () => Promise<boolean>;

let reauthHandler: ReauthHandler | null = null;
let revivalInFlight: Promise<boolean> | null = null;

/**
 * Register the window session controller's revival flow (WTK-199). One
 * handler per window — the controller owns cross-window coordination; this
 * client only stalls and replays its own requests. Pass null to deregister.
 */
export function setReauthHandler(handler: ReauthHandler | null): void {
  reauthHandler = handler;
}

function awaitRevival(handler: ReauthHandler): Promise<boolean> {
  // Single-flight: every request stalled on the same expiry shares one
  // revival prompt rather than stacking overlays.
  revivalInFlight ??= handler().finally(() => {
    revivalInFlight = null;
  });
  return revivalInFlight;
}

interface Exchange<TData> {
  status: number;
  body: Envelope<TData>;
}

async function exchange<TData>(
  path: string,
  init?: RequestInit,
): Promise<Exchange<TData>> {
  const headers = new Headers(init?.headers);
  // Identity travels as the trusted X-User-ID header — the deps.py seam.
  // The sessionReference itself is not yet consumed by read surfaces (the
  // contract gap DEC-080 records for PI-011's API side); the store still
  // holds and rotates it so that fix lands without touching callers.
  const session = readSession();
  if (session !== null && !headers.has("X-User-ID")) {
    headers.set("X-User-ID", session.userID);
  }
  const response = await fetch(path, { ...init, headers });
  const body = (await response.json()) as Envelope<TData>;
  return { status: response.status, body };
}

function isReauthRequired(body: Envelope<unknown>): boolean {
  return body.errors?.some((error) => error.code === CODE_REAUTH_REQUIRED) ?? false;
}

// TData appears only in the return type by design: the caller asserts the
// payload shape (from the generated schema.d.ts) that fetch cannot know.
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters
export async function callApi<TData>(
  path: string,
  init?: RequestInit,
): Promise<{ data: TData; meta: Record<string, unknown> }> {
  let result = await exchange<TData>(path, init);
  const handler = reauthHandler;
  if (handler !== null && isReauthRequired(result.body)) {
    // Hold the request through revival, then replay it ONCE with the fresh
    // headers (never dropped — the DEC-080 hold-and-replay invariant). A
    // replay that expires again surfaces as the error; no retry loop.
    // Replay reuses init verbatim: callers pass JSON string bodies, never
    // one-shot streams.
    if (await awaitRevival(handler)) {
      result = await exchange<TData>(path, init);
    }
  }
  if (result.body.errors !== null) {
    throw new EnvelopeError(result.status, result.body.errors, result.body.data);
  }
  return { data: result.body.data, meta: result.body.meta };
}
