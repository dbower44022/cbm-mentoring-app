/**
 * The client session state every window shares (DEC-080 boot sequence): the
 * opaque reference minted by `POST /auth/login` / `/auth/reauth` plus the
 * identity facts the server returned alongside it. Persisted in localStorage
 * so the main window and every pop-out read ONE session; cross-window
 * *signaling* (SessionRevived, SessionLoggedOut) is the window session
 * controller's job over BroadcastChannel, never this module's.
 *
 * Inputs/outputs: `readSession` returns the stored state or null (signed
 * out); `writeSession` replaces it; `clearSession` removes it. Failure mode:
 * an unreadable stored entry is cleared and read as null — signed-out, never
 * a crash loop on every subsequent call.
 */

export interface SessionState {
  // Wire names are the contract — these mirror the /auth session payload.
  sessionReference: string;
  userID: string;
  roleNames: string[];
}

const STORAGE_KEY = "mentorapp.session";

function isSessionState(value: unknown): value is SessionState {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record.sessionReference === "string" &&
    typeof record.userID === "string" &&
    Array.isArray(record.roleNames) &&
    record.roleNames.every((role) => typeof role === "string")
  );
}

/** The session this window acts as, or null when signed out. */
export function readSession(): SessionState | null {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (raw === null) {
    return null;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    parsed = null;
  }
  if (!isSessionState(parsed)) {
    clearSession();
    return null;
  }
  return parsed;
}

/** Store the session payload a successful login or re-auth returned. */
export function writeSession(state: SessionState): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

/** Forget the session (logout, or an ended session the user leaves). */
export function clearSession(): void {
  localStorage.removeItem(STORAGE_KEY);
}
