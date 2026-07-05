/**
 * The typed client for the one response shape every mentorapp endpoint speaks
 * (mirrors src/mentorapp/api/envelope.py — that module is the contract; this
 * one only transports it).
 *
 * Inputs: an API path (same-origin; the Vite dev server proxies API prefixes
 * to FastAPI) and optional fetch init. Output: the unwrapped `{data, meta}` of
 * a success envelope. Failure modes: `EnvelopeError` whenever `errors` is
 * non-null — carrying every structured entry (a multi-field save reports ALL
 * failures in one round trip) plus the failure `data` body some recoveries
 * need (current record on a 409 conflict, duplicate candidates on create).
 */

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

// TData appears only in the return type by design: the caller asserts the
// payload shape (from the generated schema.d.ts) that fetch cannot know.
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters
export async function callApi<TData>(
  path: string,
  init?: RequestInit,
): Promise<{ data: TData; meta: Record<string, unknown> }> {
  const response = await fetch(path, {
    // Session auth is cookie-borne; every call is same-origin (dev proxy).
    credentials: "same-origin",
    ...init,
  });
  const body = (await response.json()) as Envelope<TData>;
  if (body.errors !== null) {
    throw new EnvelopeError(response.status, body.errors, body.data);
  }
  return { data: body.data, meta: body.meta };
}
