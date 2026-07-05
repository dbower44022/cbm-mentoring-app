/**
 * The signed-out surface (WTK-199): login and forgot-password, rendered from
 * GET /auth/screens. Outcomes speak the served educate messages — a CRM
 * outage NEVER reads as a wrong password (signInFailureMessage), and the
 * forgot-password confirmation is the one uniform message whether or not the
 * account exists (anti-enumeration, server-guaranteed).
 */

import { type ReactElement, useEffect, useState } from "react";
import { callApi } from "../api/envelope";
import { type SessionState, writeSession } from "../session";
import { signInFailureMessage } from "./failure";
import type {
  AuthScreensPayload,
  EducateMessagePayload,
  SessionPayload,
} from "./payloads";
import { ScreenForm } from "./screen-form";

export interface SignInProps {
  onSignedIn: (session: SessionState) => void;
}

export function SignIn({ onSignedIn }: SignInProps): ReactElement {
  const { payload, loadFailed, reload } = useAuthScreens();
  const [screenKey, setScreenKey] = useState<"login" | "forgotPassword">("login");
  const [message, setMessage] = useState<EducateMessagePayload | null>(null);
  const [busy, setBusy] = useState(false);

  if (payload === null) {
    return loadFailed ? (
      <div className="shell-boot">
        <p>The sign-in screen could not load: the app could not reach its API.</p>
        <p>Check your connection and retry; if it persists, the service may be down.</p>
        <button type="button" onClick={reload}>
          Retry
        </button>
      </div>
    ) : (
      <p className="shell-boot">Loading…</p>
    );
  }

  const switchTo = (key: "login" | "forgotPassword"): void => {
    setScreenKey(key);
    setMessage(null);
  };

  const login = (values: Record<string, string>): void => {
    setBusy(true);
    const loginName = values.username ?? "";
    void callApi<SessionPayload>("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ loginName, password: values.password ?? "" }),
    })
      .then(({ data }) => {
        const session: SessionState = { ...data, loginName };
        writeSession(session);
        onSignedIn(session);
      })
      .catch((failure: unknown) => {
        setMessage(signInFailureMessage(failure, payload.messages));
      })
      .finally(() => {
        setBusy(false);
      });
  };

  const requestReset = (values: Record<string, string>): void => {
    setBusy(true);
    void callApi<{ resetRequestAccepted: boolean }>("/auth/forgot-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        loginName: values.username ?? "",
        emailAddress: values.emailAddress ?? "",
      }),
    })
      .then(() => {
        // Uniform whether or not the account exists — the CRM owns recovery.
        setMessage(payload.messages.resetRequested);
      })
      .catch((failure: unknown) => {
        // Only an outage breaks the constant answer; never a rejection here.
        setMessage(signInFailureMessage(failure, payload.messages));
      })
      .finally(() => {
        setBusy(false);
      });
  };

  return screenKey === "login" ? (
    <ScreenForm
      screen={payload.screens.login}
      busy={busy}
      message={message}
      onSubmit={login}
      onLink={() => {
        switchTo("forgotPassword");
      }}
    />
  ) : (
    <ScreenForm
      screen={payload.screens.forgotPassword}
      busy={busy}
      message={message}
      onSubmit={requestReset}
      onLink={() => {
        switchTo("login");
      }}
    />
  );
}

/**
 * Fetch the served screen declarations once; retry is the caller's button.
 * `enabled=false` defers the fetch (the re-auth overlay only needs the
 * payload once a session actually expires).
 */
export function useAuthScreens(enabled = true): {
  payload: AuthScreensPayload | null;
  loadFailed: boolean;
  reload: () => void;
} {
  const [payload, setPayload] = useState<AuthScreensPayload | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!enabled || payload !== null) {
      return;
    }
    let cancelled = false;
    void callApi<AuthScreensPayload>("/auth/screens")
      .then(({ data }) => {
        if (!cancelled) {
          setPayload(data);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLoadFailed(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, payload, attempt]);

  return {
    payload,
    loadFailed,
    reload: () => {
      setLoadFailed(false);
      setAttempt((n) => n + 1);
    },
  };
}
