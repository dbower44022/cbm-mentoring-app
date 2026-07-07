/**
 * Window session state (DEC-080 §B/§J): storage is re-exported from
 * api/session.ts — its ONE canonical home, shared with the envelope client so
 * the login that writes the session and the request that reads it can never
 * disagree on where it lives. This module owns what sits above storage: the
 * per-user BroadcastChannel that moves whole-session transitions across
 * windows (layout standard: logout is explicit and total; one re-login
 * restores every window). Identity headers are NOT built here any more
 * (FND-909 D9): the envelope client attaches the session reference itself,
 * fresh from storage on every attempt — a per-component header helper was a
 * second identity source that could go stale across an in-place re-auth.
 */

export {
  clearSession,
  readSession,
  type SessionState,
  writeSession,
} from "./api/session";

/**
 * The whole-session transitions windows broadcast to each other:
 * SessionLoggedOut ends every window together; SessionRevived (one window's
 * successful in-place re-auth) dismisses every sibling's prompt so held
 * requests replay (WTK-005's cross-window contract).
 */
export type SessionBroadcast =
  { kind: "SessionLoggedOut" } | { kind: "SessionRevived"; sessionReference: string };

function channelName(userID: string): string {
  return `mentorapp:session:${userID}`;
}

function broadcast(userID: string, message: SessionBroadcast): void {
  const channel = new BroadcastChannel(channelName(userID));
  channel.postMessage(message);
  channel.close();
}

export function broadcastSessionLoggedOut(userID: string): void {
  broadcast(userID, { kind: "SessionLoggedOut" });
}

export function broadcastSessionRevived(
  userID: string,
  sessionReference: string,
): void {
  broadcast(userID, { kind: "SessionRevived", sessionReference });
}

/** Subscribe to whole-session transitions; returns the unsubscribe. */
export function onSessionBroadcast(
  userID: string,
  handler: (message: SessionBroadcast) => void,
): () => void {
  const channel = new BroadcastChannel(channelName(userID));
  channel.onmessage = (event: MessageEvent<SessionBroadcast>) => {
    handler(event.data);
  };
  return () => {
    channel.close();
  };
}
