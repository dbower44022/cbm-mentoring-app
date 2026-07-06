/**
 * The Home panel (REQ-003/REQ-011): `GET /home` composed as the server
 * declared it — the messages dashlet always first, then the user's chosen
 * dashlets in saved order, broken ones kept visible with their educate
 * notice. Rendering IS reading (the endpoint reads what it returns), so a
 * successful load tells the host via `onMessagesViewed` — the urgent banner
 * re-fetches instead of bannering messages this view just read.
 */

import { type ReactElement, useEffect, useState } from "react";

import {
  type AdminMessagePayload,
  type DashletPayload,
  type HomePayload,
} from "../api/payloads";
import { useEnvelope } from "../api/useEnvelope";
import { DeclinedNotice, EducateNotice, UnreachableNotice } from "../shell/educate";
import { AdminMessageView } from "../shell/messages";

/** The provided, always-first dashlet's viewKey (home_panel.MESSAGES_DASHLET). */
export const MESSAGES_DASHLET_VIEW_KEY = "home.messages";

function MessagesDashlet({
  messages,
}: {
  messages: AdminMessagePayload[];
}): ReactElement {
  // Local copy so an acknowledgment click updates in place without
  // re-fetching /home (a re-fetch would be a fresh read of everything).
  const [items, setItems] = useState(messages);
  useEffect(() => {
    setItems(messages);
  }, [messages]);

  const markAcknowledged = (messageKey: string): void => {
    setItems((current) =>
      current.map((m) =>
        m.messageKey === messageKey ? { ...m, acknowledged: true } : m,
      ),
    );
  };

  if (items.length === 0) {
    return <p>No messages from your administrator right now.</p>;
  }
  return (
    <div className="message-list">
      {items.map((message) => (
        <AdminMessageView
          key={message.messageKey}
          message={message}
          onAcknowledged={markAcknowledged}
        />
      ))}
    </div>
  );
}

function Dashlet({
  dashlet,
  messages,
}: {
  dashlet: DashletPayload;
  messages: AdminMessagePayload[];
}): ReactElement {
  return (
    <section aria-label={dashlet.title} className="dashlet">
      <h2>{dashlet.title}</h2>
      {/* The body flexes to consume the dashlet's share of the panel and
          scrolls within it (REQ-091): dashlets fill Home, never idle space. */}
      <div className="dashlet-body">
        {dashlet.notice !== null ? (
          <EducateNotice notice={dashlet.notice} />
        ) : dashlet.viewKey === MESSAGES_DASHLET_VIEW_KEY ? (
          <MessagesDashlet messages={messages} />
        ) : (
          // A dashlet is a view rendered small; the grid-panel renderer that
          // draws view content is a later PI-011 slice, so a live view dashlet
          // shows its identity honestly rather than an invented mini-grid.
          <p>This dashlet shows the view '{dashlet.viewKey}'.</p>
        )}
      </div>
    </section>
  );
}

export function HomePanel({
  onMessagesViewed,
}: {
  onMessagesViewed: () => void;
}): ReactElement {
  const { state } = useEnvelope<HomePayload>("/home");

  const loaded = state.phase === "loaded";
  useEffect(() => {
    if (loaded) {
      onMessagesViewed();
    }
  }, [loaded, onMessagesViewed]);

  switch (state.phase) {
    case "loading":
      return <p>Loading Home…</p>;
    case "declined":
      return <DeclinedNotice errors={state.errors} />;
    case "unreachable":
      return <UnreachableNotice />;
    case "loaded":
      return (
        <main aria-label="Home" className="home-panel">
          {state.data.dashlets.map((dashlet) => (
            <Dashlet
              key={dashlet.viewKey}
              dashlet={dashlet}
              messages={state.data.messages}
            />
          ))}
        </main>
      );
  }
}
