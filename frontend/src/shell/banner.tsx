/**
 * The urgent-message banner every panel hosts (REQ-011): unexpired urgent
 * messages this user has NOT read, served by `GET /home/banner`. Opening a
 * banner is the read act — `POST /home/messages/{key}/read` — scoped to the
 * one message the user actually saw; the expanded message stays on screen
 * (with its acknowledgment affordance) until dismissed, but once read it
 * never banners again. `refreshToken` lets the hosting page re-fetch after
 * something else read messages (Home's own view does).
 */

import { type ReactElement, useState } from "react";

import { type ApiError, callApi, EnvelopeError } from "../api/envelope";
import { type AdminMessagePayload, type BannerPayload } from "../api/payloads";
import { useEnvelope } from "../api/useEnvelope";
import { DeclinedNotice } from "./educate";
import { AdminMessageView } from "./messages";

export function UrgentBanner({ refreshToken }: { refreshToken?: unknown }): ReactElement | null {
  const { state } = useEnvelope<BannerPayload>("/home/banner", refreshToken);
  // Messages the user opened from this banner: read server-side, kept
  // rendered here until dismissed so reading never makes content vanish.
  const [opened, setOpened] = useState<AdminMessagePayload[]>([]);
  const [errors, setErrors] = useState<ApiError[] | null>(null);

  const open = (message: AdminMessagePayload): void => {
    void callApi<AdminMessagePayload>(`/home/messages/${message.messageKey}/read`, {
      method: "POST",
    })
      .then((result) => {
        setErrors(null);
        setOpened((current) => [...current, result.data]);
      })
      .catch((failure: unknown) => {
        setErrors(failure instanceof EnvelopeError ? failure.errors : null);
      });
  };

  const markAcknowledged = (messageKey: string): void => {
    setOpened((current) =>
      current.map((m) => (m.messageKey === messageKey ? { ...m, acknowledged: true } : m)),
    );
  };

  const dismiss = (messageKey: string): void => {
    setOpened((current) => current.filter((m) => m.messageKey !== messageKey));
  };

  // The banner is chrome, not a panel: a decline or outage here renders
  // nothing rather than blocking the page — Home still shows every message.
  if (state.phase !== "loaded") {
    return null;
  }

  const openedKeys = new Set(opened.map((m) => m.messageKey));
  const unopened = state.data.messages.filter((m) => !openedKeys.has(m.messageKey));
  if (unopened.length === 0 && opened.length === 0 && errors === null) {
    return null;
  }

  return (
    <div role="alert" aria-label="Urgent messages">
      {unopened.map((message) => (
        <p key={message.messageKey}>
          Urgent message from your administrator: {message.title}{" "}
          <button type="button" onClick={() => open(message)}>
            Read message
          </button>
        </p>
      ))}
      {opened.map((message) => (
        <div key={message.messageKey}>
          <AdminMessageView message={message} onAcknowledged={markAcknowledged} />
          <button type="button" onClick={() => dismiss(message.messageKey)}>
            Dismiss
          </button>
        </div>
      ))}
      {errors !== null && <DeclinedNotice errors={errors} />}
    </div>
  );
}
