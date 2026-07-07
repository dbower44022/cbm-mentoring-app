/**
 * The typed client for the one response shape every mentorapp endpoint speaks
 * (mirrors src/mentorapp/api/envelope.py — that module is the contract; this
 * one only transports it), and the ONE interception point for session
 * transitions (DEC-080): a `reauthRequired` refusal hands off to the
 * registered window session controller (hold-and-replay), and an
 * `unauthenticated` refusal on ANY surface notifies the controller that the
 * session is beyond revival (FND-909 D9 — a stale reference over a rebuilt
 * database must land the user signed out, never leave a read surface holding
 * a raw 401).
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

/** The refusal code for a session beyond revival (unknown/ended reference). */
export const CODE_UNAUTHENTICATED = "unauthenticated";

/**
 * Revive the expired session — overlay REAUTH_SCREEN in place, run
 * `POST /auth/reauth`, store the rotated reference — and resolve true once
 * revived (false when the session is beyond revival, e.g. ENDED).
 */
export type ReauthHandler = () => Promise<boolean>;

/** Told once per `unauthenticated` refusal: this window's session is dead. */
export type SessionEndedHandler = () => void;

let reauthHandler: ReauthHandler | null = null;
let sessionEndedHandler: SessionEndedHandler | null = null;
let revivalInFlight: Promise<boolean> | null = null;

/**
 * Register the window session controller's revival flow (WTK-199). One
 * handler per window — the controller owns cross-window coordination; this
 * client only stalls and replays its own requests. Pass null to deregister.
 */
export function setReauthHandler(handler: ReauthHandler | null): void {
  reauthHandler = handler;
}

/**
 * Register the controller's beyond-revival flow (FND-909 D9): every surface's
 * `unauthenticated` refusal routes here, so a grid read against a dead
 * session shows the session-ended screen instead of a component-local error.
 * The refusal still throws to the caller — this only ADDS the notification.
 */
export function setSessionEndedHandler(handler: SessionEndedHandler | null): void {
  sessionEndedHandler = handler;
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
  // The session reference is the ONLY identity the client sends; the server
  // resolves WHO from it (FND-909 D9 — the old client-claimed X-User-ID
  // header let any caller act as any user, and is gone). Read fresh from
  // storage on EVERY attempt and always overwrite: a held request replayed
  // after in-place re-auth must carry the rotated reference, never the dead
  // one captured in its original init.
  const session = readSession();
  if (session !== null) {
    headers.set("X-Session-Reference", session.sessionReference);
  }
  const response = await fetch(path, { ...init, headers });
  const body = (await response.json()) as Envelope<TData>;
  return { status: response.status, body };
}

function hasCode(body: Envelope<unknown>, code: string): boolean {
  return body.errors?.some((error) => error.code === code) ?? false;
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
  if (handler !== null && hasCode(result.body, CODE_REAUTH_REQUIRED)) {
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
    if (sessionEndedHandler !== null && hasCode(result.body, CODE_UNAUTHENTICATED)) {
      // Beyond revival, on WHATEVER surface hit it (FND-909 D9): tell the
      // window session controller so the user lands on the session-ended
      // screen → sign in, instead of a component-local dead end. /auth is
      // no exception — the only /auth endpoint that answers unauthenticated
      // is a re-auth against a dead session, the same terminal state.
      sessionEndedHandler();
    }
    throw new EnvelopeError(result.status, result.body.errors, result.body.data);
  }
  return { data: result.body.data, meta: result.body.meta };
}
