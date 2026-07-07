/**
 * Failure → educate-message selection for the credential screens: the client
 * half of auth_flows.login_failure_message. Input: whatever the envelope
 * client threw, plus the served message set. Output: the message to render.
 * Failure mode: none — every input maps to a message.
 */

import { EnvelopeError } from "../api/envelope";
import type { AuthScreensPayload, EducateMessagePayload } from "./payloads";

export const CODE_INVALID_CREDENTIALS = "invalidCredentials";
export const CODE_REAUTH_IDENTITY_MISMATCH = "reauthIdentityMismatch";
// The beyond-revival code lives with the envelope client (its interception
// point since FND-909 D9); re-exported so the auth screens keep one import.
export { CODE_UNAUTHENTICATED } from "../api/envelope";

/** True when the refusal envelope carries the given code. */
export function hasCode(failure: unknown, code: string): boolean {
  return (
    failure instanceof EnvelopeError &&
    failure.errors.some((error) => error.code === code)
  );
}

/**
 * Pick the sign-in outcome message. Rejected ONLY on the positive
 * invalidCredentials refusal; every other failure — crmUnavailable, an
 * unreachable network, a code this screen has never seen — presents as the
 * CRM-unavailable message. Failing toward "not your fault" is deliberate
 * (auth_flows.login_failure_message): a message may only blame the user's
 * input when the CRM actually judged it.
 */
export function signInFailureMessage(
  failure: unknown,
  messages: AuthScreensPayload["messages"],
): EducateMessagePayload {
  if (hasCode(failure, CODE_INVALID_CREDENTIALS)) {
    return messages.signInRejected;
  }
  return messages.signInCrmUnavailable;
}
