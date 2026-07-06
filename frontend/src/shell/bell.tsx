/**
 * The header notification bell (REQ-014): badge from `GET /shell/bell`'s
 * `meta.unreadCount`, dropdown listing the unread entries. Opening the
 * dropdown is the view that acknowledges — it snapshots the entries for
 * display, then `POST /shell/bell/read` stamps them and the badge re-fetches
 * to the server's count. A GET never stamps anything (safe to repeat), so
 * the snapshot is what keeps just-read entries visible for this open.
 */

import { type ReactElement, useState } from "react";

import { type ApiError, callApi, EnvelopeError } from "../api/envelope";
import { type BellEntryPayload, type BellPayload } from "../api/payloads";
import { useEnvelope } from "../api/useEnvelope";
import { DeclinedNotice } from "./educate";

export function NotificationBell(): ReactElement {
  const { state, reload } = useEnvelope<BellPayload>("/shell/bell");
  const [dropdown, setDropdown] = useState<{ entries: BellEntryPayload[] } | null>(
    null,
  );
  const [errors, setErrors] = useState<ApiError[] | null>(null);

  const unreadCount =
    state.phase === "loaded" && typeof state.meta.unreadCount === "number"
      ? state.meta.unreadCount
      : 0;

  const toggle = (): void => {
    if (dropdown !== null) {
      setDropdown(null);
      return;
    }
    const entries = state.phase === "loaded" ? state.data.entries : [];
    setDropdown({ entries });
    if (entries.length === 0) {
      return;
    }
    void callApi<{ markedRead: number }>("/shell/bell/read", { method: "POST" })
      .then(() => {
        setErrors(null);
        reload();
      })
      .catch((failure: unknown) => {
        // The entries stay unread server-side — the badge keeps saying so;
        // a decline still renders rather than disappearing.
        setErrors(failure instanceof EnvelopeError ? failure.errors : null);
      });
  };

  return (
    <div>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={dropdown !== null}
        aria-label={`Notifications (${String(unreadCount)} unread)`}
      >
        Notifications{unreadCount > 0 ? ` (${String(unreadCount)})` : ""}
      </button>
      {dropdown !== null && (
        <div role="region" aria-label="Notifications">
          {dropdown.entries.length === 0 ? (
            <p>Nothing new. Completed background tasks and alerts will appear here.</p>
          ) : (
            <ul className="bell-entries">
              {dropdown.entries.map((entry) => (
                <li key={entry.notificationID}>
                  {entry.notificationMessage}{" "}
                  <time dateTime={entry.createdAt}>
                    {new Date(entry.createdAt).toLocaleString()}
                  </time>
                </li>
              ))}
            </ul>
          )}
          {errors !== null && <DeclinedNotice errors={errors} />}
        </div>
      )}
    </div>
  );
}
