/**
 * Window session state (DEC-080 §B/§J): the sessionReference + userID that
 * POST /auth/login returns, shared across windows via localStorage so every
 * window — main or pop-out — boots from the same signed-in identity.
 *
 * Identity travels as the X-User-ID header (mirrors deps.get_current_user_id,
 * the ONE identity seam server-side); when bearer auth lands it rebinds here,
 * no screen changes. Whole-session transitions broadcast on the per-user
 * BroadcastChannel so all windows move together (layout standard: logout is
 * explicit and total across windows).
 */

export interface SessionState {
  sessionReference: string;
  userID: string;
  roleNames: string[];
}

const STORAGE_KEY = "mentorapp:session";

export function readSession(): SessionState | null {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (raw === null) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as Partial<SessionState>;
    if (
      typeof parsed.sessionReference === "string" &&
      typeof parsed.userID === "string" &&
      Array.isArray(parsed.roleNames)
    ) {
      return {
        sessionReference: parsed.sessionReference,
        userID: parsed.userID,
        roleNames: parsed.roleNames.filter((r): r is string => typeof r === "string"),
      };
    }
  } catch {
    // Malformed storage degrades to signed-out, mirroring how the server
    // parses stale navigation documents: degrade, never fail the window.
  }
  return null;
}

export function writeSession(session: SessionState): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
}

export function clearSession(): void {
  localStorage.removeItem(STORAGE_KEY);
}

/** Request headers that carry the acting user on every non-/auth call. */
export function userHeaders(session: SessionState): Record<string, string> {
  return { "X-User-ID": session.userID };
}

/** One cross-window message today; reauth/revive messages land with WTK-199. */
export interface SessionBroadcast {
  kind: "SessionLoggedOut";
}

function channelName(userID: string): string {
  return `mentorapp:session:${userID}`;
}

export function broadcastSessionLoggedOut(userID: string): void {
  const channel = new BroadcastChannel(channelName(userID));
  channel.postMessage({ kind: "SessionLoggedOut" } satisfies SessionBroadcast);
  channel.close();
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
