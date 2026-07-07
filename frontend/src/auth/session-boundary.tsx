/**
 * The WindowSessionController port (WTK-199, mirroring
 * src/mentorapp/ui/auth_flows.WindowSessionController): one per window, owning
 * the three invariants the standards demand —
 *
 * - Unsaved work is NEVER cleared by a session transition: the re-auth prompt
 *   and the ended screen OVERLAY the window (children stay mounted behind
 *   them); no navigation, no reload.
 * - Requests raised while re-auth is pending are held and replayed, never
 *   dropped: this controller is the ReauthHandler the envelope client stalls
 *   on (setReauthHandler); resolving true replays every held request.
 * - Whole-session transitions move every window together: one successful
 *   re-login broadcasts SessionRevived so every sibling dismisses its prompt
 *   and replays; logout broadcasts SessionLoggedOut and is total.
 */

import { type ReactElement, type ReactNode, useEffect, useRef, useState } from "react";
import { callApi, setReauthHandler, setSessionEndedHandler } from "../api/envelope";
import {
  broadcastSessionLoggedOut,
  broadcastSessionRevived,
  clearSession,
  onSessionBroadcast,
  readSession,
  type SessionState,
  writeSession,
} from "../session";
import {
  CODE_REAUTH_IDENTITY_MISMATCH,
  CODE_UNAUTHENTICATED,
  hasCode,
  signInFailureMessage,
} from "./failure";
import type { EducateMessagePayload, SessionPayload } from "./payloads";
import { EducateNotice, ScreenForm } from "./screen-form";
import { useAuthScreens } from "./sign-in";

type Phase = "active" | "reauthPrompt" | "ended";

export interface SessionBoundaryProps {
  session: SessionState;
  /** A revival rotated the reference; the app adopts the fresh session. */
  onSessionChanged: (session: SessionState) => void;
  /** The user explicitly left (logout, or leaving an ended session). */
  onSignedOut: () => void;
  children: ReactNode;
}

export function SessionBoundary({
  session,
  onSessionChanged,
  onSignedOut,
  children,
}: SessionBoundaryProps): ReactElement {
  const [phase, setPhase] = useState<Phase>("active");
  const [message, setMessage] = useState<EducateMessagePayload | null>(null);
  const [busy, setBusy] = useState(false);
  const resolveRevival = useRef<((revived: boolean) => void) | null>(null);
  // Mirror for the broadcast handler: it must read the CURRENT phase without
  // re-subscribing the channel on every transition.
  const phaseRef = useRef<Phase>("active");
  phaseRef.current = phase;
  const { payload, loadFailed, reload } = useAuthScreens(phase !== "active");

  const settle = (revived: boolean): void => {
    resolveRevival.current?.(revived);
    resolveRevival.current = null;
  };

  useEffect(() => {
    // The envelope client stalls every expired request on this promise and
    // replays on true — the DEC-080 hold-and-replay seam, one handler per
    // window (single-flight lives in the client).
    setReauthHandler(
      () =>
        new Promise<boolean>((resolve) => {
          resolveRevival.current = resolve;
          setMessage(null);
          setPhase("reauthPrompt");
        }),
    );
    return () => {
      setReauthHandler(null);
    };
  }, []);

  useEffect(() => {
    // Any surface's `unauthenticated` refusal — a grid read, /home, a write —
    // means the session is beyond revival (FND-909 D9: typically a stale
    // reference the server no longer knows, e.g. after a database rebuild).
    // Land on the ended screen: work stays mounted behind the overlay, and
    // the user's explicit "Sign in again" leads to the login screen — never
    // a component-local dead end, never a crash.
    setSessionEndedHandler(() => {
      settle(false);
      setPhase("ended");
    });
    return () => {
      setSessionEndedHandler(null);
    };
  }, []);

  useEffect(() => {
    return onSessionBroadcast(session.userID, (broadcast) => {
      if (broadcast.kind === "SessionRevived") {
        // A sibling revived the shared session; adopt it and replay. An
        // already-ended window stays ended (its user saw the explanation).
        if (phaseRef.current !== "reauthPrompt") {
          return;
        }
        const revived = readSession();
        if (revived !== null) {
          onSessionChanged(revived);
        }
        settle(true);
        setPhase("active");
      } else {
        settle(false);
        setPhase("ended");
      }
    });
    // Depends on the user only: onSessionChanged is the app root's state
    // setter (stable), and phase is read through phaseRef so a transition
    // never tears down the channel mid-broadcast.
  }, [session.userID]);

  const reauthenticate = (values: Record<string, string>): void => {
    if (payload === null) {
      return;
    }
    setBusy(true);
    void callApi<SessionPayload>("/auth/reauth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sessionReference: session.sessionReference,
        loginName: session.loginName,
        password: values.password ?? "",
      }),
    })
      .then(({ data }) => {
        const revived: SessionState = { ...data, loginName: session.loginName };
        writeSession(revived);
        broadcastSessionRevived(revived.userID, revived.sessionReference);
        onSessionChanged(revived);
        settle(true);
        setPhase("active");
        setMessage(null);
      })
      .catch((failure: unknown) => {
        if (hasCode(failure, CODE_UNAUTHENTICATED)) {
          // Beyond revival (ended, or grace lapsed): terminal, work kept.
          settle(false);
          setPhase("ended");
        } else if (hasCode(failure, CODE_REAUTH_IDENTITY_MISMATCH)) {
          setMessage(payload.messages.reauthWrongUser);
        } else {
          setMessage(signInFailureMessage(failure, payload.messages));
        }
      })
      .finally(() => {
        setBusy(false);
      });
  };

  const logout = (): void => {
    // "Log out instead" from the prompt is an explicit act; the prompt itself
    // guards no work (edits stay in the window behind it), so no dirty guard
    // here — the editors' own guards run when they exist (PI-003+).
    void callApi<{ loggedOut: boolean }>("/auth/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionReference: session.sessionReference }),
    }).finally(() => {
      clearSession();
      broadcastSessionLoggedOut(session.userID);
      settle(false);
      onSignedOut();
    });
  };

  const leaveEndedSession = (): void => {
    clearSession();
    onSignedOut();
  };

  return (
    <>
      {children}
      {phase !== "active" && (
        <div className="auth-overlay" role="dialog" aria-modal="true">
          {payload === null ? (
            <div className="auth-screen">
              {loadFailed ? (
                <>
                  <p>The sign-in prompt could not load; the API is unreachable.</p>
                  <button type="button" onClick={reload}>
                    Retry
                  </button>
                </>
              ) : (
                <p>Loading…</p>
              )}
            </div>
          ) : phase === "reauthPrompt" ? (
            <div className="auth-overlay-panel">
              <EducateNotice message={payload.messages.reauthPrompt} />
              <ScreenForm
                screen={payload.screens.reauth}
                fixedValues={{ username: session.loginName }}
                busy={busy}
                message={message}
                onSubmit={reauthenticate}
                onLink={logout}
              />
            </div>
          ) : (
            <div className="auth-overlay-panel">
              <EducateNotice message={payload.messages.sessionEnded} />
              <button type="button" className="auth-submit" onClick={leaveEndedSession}>
                Sign in again
              </button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
