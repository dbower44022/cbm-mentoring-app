/**
 * App root (DEC-080 §B): establish session state, then render the shell for
 * this window's kind. The stored session (written by POST /auth/login) is
 * shared across windows; a window without one renders the signed-out state —
 * the login screen itself renders with WTK-199. A SessionLoggedOut broadcast
 * from any window ends this one too: logout is explicit and total.
 */

import { type ReactElement, useEffect, useState } from "react";
import { onSessionBroadcast, readSession, type SessionState } from "./session";
import { Shell } from "./shell/shell";

export function App(): ReactElement {
  const [session, setSession] = useState<SessionState | null>(readSession);

  useEffect(() => {
    if (session === null) {
      return;
    }
    // SessionLoggedOut is the only broadcast today; reauth messages (WTK-199)
    // will branch on message.kind when they join the union.
    return onSessionBroadcast(session.userID, () => {
      setSession(null);
    });
  }, [session]);

  if (session === null) {
    return (
      <div className="shell-boot">
        <p>You are signed out.</p>
        <p>
          Signing in ends up here once the sign-in screen ships (WTK-199). Until then, a
          session established by POST /auth/login is picked up from this browser
          automatically.
        </p>
      </div>
    );
  }

  return (
    <Shell
      session={session}
      onLoggedOut={() => {
        setSession(null);
      }}
    />
  );
}
