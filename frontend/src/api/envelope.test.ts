/**
 * Component tests for the shared client engine: the session store and the
 * envelope client's session seam (identity header, reauthRequired
 * hold-and-replay, single-flight revival). Pure-logic tests over stubbed
 * fetch/localStorage — no DOM, no server.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  callApi,
  EnvelopeError,
  setReauthHandler,
  setSessionEndedHandler,
} from "./envelope";
import { clearSession, readSession, type SessionState, writeSession } from "./session";

const SESSION: SessionState = {
  sessionReference: "ref-1",
  userID: "user-1",
  roleNames: ["mentor"],
  loginName: "mentor-1",
};

function memoryStorage(): Pick<Storage, "getItem" | "setItem" | "removeItem"> {
  const entries = new Map<string, string>();
  return {
    getItem: (key) => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, value),
    removeItem: (key) => entries.delete(key),
  };
}

function envelopeResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

const REAUTH_REFUSAL = {
  data: null,
  meta: {},
  errors: [
    {
      fieldName: null,
      code: "reauthRequired",
      message: "The session expired; re-authenticate to continue.",
    },
  ],
};

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage());
});

const UNAUTHENTICATED_REFUSAL = {
  data: null,
  meta: {},
  errors: [
    {
      fieldName: null,
      code: "unauthenticated",
      message: "A valid session is required; sign in again.",
    },
  ],
};

afterEach(() => {
  setReauthHandler(null);
  setSessionEndedHandler(null);
  vi.unstubAllGlobals();
});

describe("session store", () => {
  it("round-trips the session payload", () => {
    writeSession(SESSION);
    expect(readSession()).toEqual(SESSION);
    clearSession();
    expect(readSession()).toBeNull();
  });

  it("reads a corrupt stored entry as signed out and clears it", () => {
    localStorage.setItem("mentorapp.session", "{not json");
    expect(readSession()).toBeNull();
    expect(localStorage.getItem("mentorapp.session")).toBeNull();
  });
});

describe("callApi", () => {
  it("unwraps a success envelope", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        envelopeResponse({ data: { ok: true }, meta: { totalCount: 1 }, errors: null }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const result = await callApi<{ ok: boolean }>("/shell");

    expect(result).toEqual({ data: { ok: true }, meta: { totalCount: 1 } });
  });

  it("throws EnvelopeError carrying every error and the failure data", async () => {
    const refusal = {
      data: { currentRecord: "server copy" },
      meta: {},
      errors: [
        { fieldName: "name", code: "required", message: "Name is required." },
        { fieldName: null, code: "conflict", message: "The record changed." },
      ],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(envelopeResponse(refusal, 409)));

    const failure = await callApi("/records/contact/abc").catch((e: unknown) => e);

    expect(failure).toBeInstanceOf(EnvelopeError);
    const error = failure as EnvelopeError;
    expect(error.status).toBe(409);
    expect(error.errors).toHaveLength(2);
    expect(error.data).toEqual({ currentRecord: "server copy" });
  });

  it("sends the session reference from the store, and nothing when signed out", async () => {
    // FND-909 D9: the opaque reference is the ONLY identity sent — the
    // server resolves the user; no client-claimed X-User-ID travels.
    const fetchMock = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(envelopeResponse({ data: null, meta: {}, errors: null })),
      );
    vi.stubGlobal("fetch", fetchMock);

    await callApi("/shell");
    writeSession(SESSION);
    await callApi("/shell");

    const [, anonymousInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    const [, signedInInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(new Headers(anonymousInit.headers).get("X-Session-Reference")).toBeNull();
    const signedInHeaders = new Headers(signedInInit.headers);
    expect(signedInHeaders.get("X-Session-Reference")).toBe("ref-1");
    expect(signedInHeaders.get("X-User-ID")).toBeNull();
  });

  it("holds a reauthRequired request through revival and replays it once", async () => {
    writeSession(SESSION);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(envelopeResponse(REAUTH_REFUSAL, 401))
      .mockResolvedValueOnce(
        envelopeResponse({ data: { replayed: true }, meta: {}, errors: null }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const handler = vi.fn().mockImplementation(() => {
      // The controller stores the rotated reference before resolving.
      writeSession({ ...SESSION, sessionReference: "ref-2", userID: "user-1" });
      return Promise.resolve(true);
    });
    setReauthHandler(handler);

    const result = await callApi<{ replayed: boolean }>("/shell");

    expect(result.data).toEqual({ replayed: true });
    expect(handler).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    // The replay reads the store FRESH: it carries the rotated reference,
    // never the dead one the original attempt was built with.
    const [, replayInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(new Headers(replayInit.headers).get("X-Session-Reference")).toBe("ref-2");
  });

  it("shares one revival across concurrent stalled requests", async () => {
    writeSession(SESSION);
    let revived = false;
    const fetchMock = vi.fn().mockImplementation(() => {
      return Promise.resolve(
        revived
          ? envelopeResponse({ data: "fresh", meta: {}, errors: null })
          : envelopeResponse(REAUTH_REFUSAL, 401),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const handler = vi.fn().mockImplementation(async () => {
      await Promise.resolve();
      revived = true;
      return true;
    });
    setReauthHandler(handler);

    const [first, second] = await Promise.all([
      callApi<string>("/shell"),
      callApi<string>("/home"),
    ]);

    expect(first.data).toBe("fresh");
    expect(second.data).toBe("fresh");
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("surfaces the refusal when revival fails, and never loops on replay", async () => {
    writeSession(SESSION);
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation(() =>
          Promise.resolve(envelopeResponse(REAUTH_REFUSAL, 401)),
        ),
    );

    setReauthHandler(() => Promise.resolve(false));
    const unrevived = await callApi("/shell").catch((e: unknown) => e);
    expect(unrevived).toBeInstanceOf(EnvelopeError);

    setReauthHandler(() => Promise.resolve(true));
    const expiredAgain = await callApi("/shell").catch((e: unknown) => e);
    expect(expiredAgain).toBeInstanceOf(EnvelopeError);
    expect((expiredAgain as EnvelopeError).errors[0]?.code).toBe("reauthRequired");
  });

  it("throws the refusal untouched when no handler is registered", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(envelopeResponse(REAUTH_REFUSAL, 401)),
    );

    const failure = await callApi("/shell").catch((e: unknown) => e);

    expect(failure).toBeInstanceOf(EnvelopeError);
  });

  // FND-909 D9: the two 401 shapes route to DIFFERENT controller flows on
  // every surface — expired-but-revivable re-auths in place; beyond-revival
  // (a stale/unknown reference, e.g. over a rebuilt database) signs out.
  it("routes an unauthenticated refusal to the session-ended flow and still throws", async () => {
    writeSession(SESSION);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(envelopeResponse(UNAUTHENTICATED_REFUSAL, 401)),
    );
    const reauth = vi.fn().mockResolvedValue(true);
    const ended = vi.fn();
    setReauthHandler(reauth);
    setSessionEndedHandler(ended);

    const failure = await callApi("/home").catch((e: unknown) => e);

    expect(failure).toBeInstanceOf(EnvelopeError);
    expect((failure as EnvelopeError).errors[0]?.code).toBe("unauthenticated");
    expect(ended).toHaveBeenCalledTimes(1);
    expect(reauth).not.toHaveBeenCalled();
  });

  it("routes a reauthRequired refusal to revival, never to the ended flow", async () => {
    writeSession(SESSION);
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(envelopeResponse(REAUTH_REFUSAL, 401))
        .mockResolvedValueOnce(
          envelopeResponse({ data: { ok: true }, meta: {}, errors: null }),
        ),
    );
    const reauth = vi.fn().mockResolvedValue(true);
    const ended = vi.fn();
    setReauthHandler(reauth);
    setSessionEndedHandler(ended);

    const result = await callApi<{ ok: boolean }>("/grids/engagements/rows");

    expect(result.data).toEqual({ ok: true });
    expect(reauth).toHaveBeenCalledTimes(1);
    expect(ended).not.toHaveBeenCalled();
  });
});
