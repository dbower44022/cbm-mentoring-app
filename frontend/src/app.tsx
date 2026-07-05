/**
 * App root (DEC-080 §B): establish session state, then render the shell for
 * this window's kind. Signed out renders the served login screen (WTK-199);
 * signed in wraps the shell in the SessionBoundary — the window session
 * controller that overlays in-place re-auth on expiry (unsaved work survives
 * behind it; one re-login restores every window) and keeps an ended session's
 * work on screen until the user explicitly leaves.
 */

import { type ReactElement, useState } from "react";
import { readSession, type SessionState } from "./session";
import { SessionBoundary } from "./auth/session-boundary";
import { SignIn } from "./auth/sign-in";
import { Shell } from "./shell/shell";

export function App(): ReactElement {
  const [session, setSession] = useState<SessionState | null>(readSession);

  if (session === null) {
    return <SignIn onSignedIn={setSession} />;
  }

  return (
    <SessionBoundary
      // Remount per identity: a different user signing in must never inherit
      // the previous session's phase or held state.
      key={session.userID}
      session={session}
      onSessionChanged={setSession}
      onSignedOut={() => {
        setSession(null);
      }}
    >
      <Shell
        session={session}
        onLoggedOut={() => {
          setSession(null);
        }}
      />
    </SessionBoundary>
  );
}
