/**
 * Component tests for the sign-in failure mapping — the client half of
 * auth_flows.login_failure_message. The load-bearing assertion is the WTK-003
 * invariant: only a positive invalidCredentials refusal may render as a wrong
 * password; an outage, a network failure, or an unknown code never does.
 */

import { describe, expect, it } from "vitest";

import { EnvelopeError } from "../api/envelope";
import { hasCode, signInFailureMessage } from "./failure";
import type { AuthScreensPayload } from "./payloads";

function message(label: string): AuthScreensPayload["messages"]["signInRejected"] {
  return { whatHappened: label, why: `${label}-why`, whatNext: `${label}-next` };
}

const MESSAGES: AuthScreensPayload["messages"] = {
  signInRejected: message("rejected"),
  signInCrmUnavailable: message("outage"),
  resetRequested: message("reset"),
  reauthPrompt: message("prompt"),
  reauthWrongUser: message("wrongUser"),
  sessionEnded: message("ended"),
};

function refusal(status: number, code: string): EnvelopeError {
  return new EnvelopeError(
    status,
    [{ fieldName: null, code, message: `${code} refusal` }],
    null,
  );
}

describe("signInFailureMessage", () => {
  it("renders the rejection ONLY when the CRM positively refused", () => {
    const picked = signInFailureMessage(refusal(401, "invalidCredentials"), MESSAGES);
    expect(picked).toBe(MESSAGES.signInRejected);
  });

  it("renders a CRM outage as not-your-fault, never a wrong password", () => {
    const picked = signInFailureMessage(refusal(503, "crmUnavailable"), MESSAGES);
    expect(picked).toBe(MESSAGES.signInCrmUnavailable);
    expect(picked).not.toBe(MESSAGES.signInRejected);
  });

  it("fails toward not-your-fault on codes it has never seen", () => {
    expect(signInFailureMessage(refusal(500, "internalError"), MESSAGES)).toBe(
      MESSAGES.signInCrmUnavailable,
    );
  });

  it("fails toward not-your-fault when the API was never reached", () => {
    expect(signInFailureMessage(new TypeError("fetch failed"), MESSAGES)).toBe(
      MESSAGES.signInCrmUnavailable,
    );
  });
});

describe("hasCode", () => {
  it("matches a refusal code inside an EnvelopeError", () => {
    expect(hasCode(refusal(401, "unauthenticated"), "unauthenticated")).toBe(true);
    expect(hasCode(refusal(401, "unauthenticated"), "reauthRequired")).toBe(false);
  });

  it("never matches a non-envelope failure", () => {
    expect(hasCode(new Error("boom"), "unauthenticated")).toBe(false);
  });
});
