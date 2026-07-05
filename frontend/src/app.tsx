/**
 * App root (DEC-080 §B): establish session state, then render the shell for
 * this window's kind. The stored session (written by POST /auth/login) is
 * shared across windows; a window without one renders the signed-out state —
 * the login screen itself renders with WTK-199. A SessionLoggedOut broadcast
 * from any window ends this one too: logout is explicit and total.
 *
 * Signed in, the routes split by window kind (WTK-198, replacing the WTK-194
 * boot screen): `/records/:entityType/:recordId` is the pop-out record
 * window; every other path renders the shell, whose Home panel hosts the
 * messaging surfaces. Every surface renders server view-models verbatim —
 * the Python modules in src/mentorapp/ui/ stay the single source of behavior.
 */

import { type ReactElement, useEffect, useState } from "react";
import { Route, Routes } from "react-router-dom";

import { onSessionBroadcast, readSession, type SessionState } from "./session";
import { Shell } from "./shell/shell";
import { RecordWindow } from "./windows/record";

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
    <Routes>
      <Route path="/records/:entityType/:recordId" element={<RecordWindow />} />
      <Route
        path="*"
        element={
          <Shell
            session={session}
            onLoggedOut={() => {
              setSession(null);
            }}
          />
        }
      />
    </Routes>
  );
}
