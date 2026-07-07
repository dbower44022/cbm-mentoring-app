/**
 * The engagement preview content (WTK-168/232, REQ-073/074/088): rendered
 * inside the grid's docked RecordPreview surface for the engagement
 * sources. LEADS with notes + open action items aggregated across ALL the
 * engagement's sessions, newest first (REQ-073/074) — the mentor never
 * opens sessions one by one to find them — then the engagement facts, the
 * client/company/contact click-throughs (real pop-out windows via the one
 * canonical opener, windows/record.popOutRecord), and the session list
 * with prep-surface links. Read-optimized: zero edit controls, per the
 * pane's declaration.
 *
 * REQ-088 (Doug's 2026-07-06 ruling): the rollup FILLS the pane's free
 * vertical space — grow one entry per measured render until visually full,
 * continues-indicator when more exists, re-fill on resize. The pure step
 * logic lives in ./rollup.ts (prototype/app.js renderPreview is the
 * reference); this component only measures.
 */

import { type ReactElement, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useEnvelope } from "../api/useEnvelope";
import { DeclinedNotice, UnreachableNotice } from "../shell/educate";
import { popOutRecord } from "../windows/record";
import {
  continuesIndicator,
  type FillState,
  fillSettled,
  grow,
  initialFill,
} from "./rollup";
import { type EngagementRollupPayload, type RollupSessionPayload } from "./payloads";

function shortDate(iso: string | null): string {
  return iso === null ? "—" : new Date(iso).toLocaleDateString();
}

/** One rollup entry: the session's notes and action items, data-dense. */
function RollupItem({ entry }: { entry: RollupSessionPayload }): ReactElement {
  return (
    <div className={entry.actionItems !== null ? "rollup-item open-ai" : "rollup-item"}>
      <div className="rollup-item-head">
        Session {shortDate(entry.scheduledAt)} — notes &amp; action items
      </div>
      {/* The rich-text fields hold clean semantic HTML from the one entry
          control (REQ-090); rendering it as HTML is the read side of that
          same contract. */}
      {entry.sessionNotes !== null ? (
        <div dangerouslySetInnerHTML={{ __html: entry.sessionNotes }} />
      ) : null}
      {entry.actionItems !== null ? (
        <div dangerouslySetInnerHTML={{ __html: entry.actionItems }} />
      ) : null}
    </div>
  );
}

export function EngagementPreview({ recordId }: { recordId: string }): ReactElement {
  const { state } = useEnvelope<EngagementRollupPayload>(
    `/engagements/${recordId}/rollup`,
  );

  switch (state.phase) {
    case "loading":
      return <p role="status">Loading engagement…</p>;
    case "declined":
      return <DeclinedNotice errors={state.errors} />;
    case "unreachable":
      return <UnreachableNotice />;
    case "loaded":
      return <LoadedPreview key={recordId} data={state.data} />;
  }
}

function LoadedPreview({ data }: { data: EngagementRollupPayload }): ReactElement {
  const navigate = useNavigate();
  const paneRef = useRef<HTMLDivElement>(null);
  const [fill, setFill] = useState<FillState>(() => initialFill(data.rollup.length));

  // The REQ-088 fill pass: after each render, measure; while free space
  // remains and more rollup exists, grow by one and measure again — the
  // converged state is "visually full", never a fixed count. A SETTLED
  // measurement schedules no update at all (not even a bailed-out one):
  // scheduling one every pass kept the commit-phase update counter climbing
  // and React threw "Maximum update depth exceeded" the first time a rollup
  // had real entries — the loop must terminate by NOT calling setFill.
  useLayoutEffect(() => {
    const pane = paneRef.current;
    if (pane === null) {
      return;
    }
    // +2 tolerates sub-pixel rounding, exactly the prototype's fill pass.
    const fits = pane.scrollHeight <= pane.clientHeight + 2;
    if (!fillSettled(fill, fits)) {
      setFill(grow(fill, fits));
    }
  }, [fill]);

  // Re-fill on resize (REQ-088): restart from the floor so a SHRUNK pane
  // sheds entries too, then the grow loop re-converges.
  useEffect(() => {
    const onResize = (): void => {
      setFill(initialFill(data.rollup.length));
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
    };
  }, [data.rollup.length]);

  const engagement = data.engagement;
  const indicator = continuesIndicator(fill);

  return (
    <div ref={paneRef} className="engagement-preview" aria-label="Engagement preview">
      <h2>{engagement.engagementName ?? "Engagement"}</h2>
      <p className="preview-sub">
        {engagement.engagementStatusLabel !== null ? (
          <span className="status-chip">{engagement.engagementStatusLabel}</span>
        ) : null}{" "}
        · {data.stats.totalSessions} sessions
      </p>

      {/* REQ-073/074: the rollup LEADS. */}
      <section className="preview-section">
        <h3>Notes &amp; open action items (all sessions)</h3>
        {data.rollup.length === 0 ? (
          <p className="preview-hint">
            No session notes yet — notes and action items aggregate here from each
            session (you never open sessions one by one to find them).
          </p>
        ) : (
          <>
            {data.rollup.slice(0, fill.shown).map((entry) => (
              <RollupItem key={entry.sessionID} entry={entry} />
            ))}
            {indicator !== null ? <p className="preview-hint">{indicator}</p> : null}
          </>
        )}
      </section>

      <section className="preview-section">
        <h3>Engagement</h3>
        <dl className="kv">
          <dt>Summary</dt>
          <dd
            dangerouslySetInnerHTML={{
              __html: engagement.engagementSummary ?? "—",
            }}
          />
          <dt>Next session</dt>
          <dd>{shortDate(data.stats.nextSessionAt)}</dd>
          <dt>Last session</dt>
          <dd>{shortDate(data.stats.lastSessionAt)}</dd>
        </dl>
      </section>

      {/* REQ-074: client, company, contacts — click-through to pop-ups
          through the one canonical opener. */}
      <section className="preview-section">
        <h3>Client · Company · Contacts (click to open)</h3>
        {data.client !== null ? (
          <button
            type="button"
            className="link-row"
            onClick={() => {
              if (data.client !== null) {
                popOutRecord("client", data.client.clientID);
              }
            }}
          >
            Client — {data.client.clientProgram ?? data.client.crmCompanyID ?? "record"}
          </button>
        ) : null}
        {data.client?.crmCompanyRefID != null ? (
          <button
            type="button"
            className="link-row"
            onClick={() => {
              if (data.client?.crmCompanyRefID != null) {
                popOutRecord("crmCompanyRef", data.client.crmCompanyRefID);
              }
            }}
          >
            Company — {data.client.crmCompanyID}
          </button>
        ) : null}
        {data.contacts.map((contact) => (
          <span key={contact.contactEmail ?? contact.contactName} className="link-row">
            {contact.contactName}
            {contact.contactEmail !== null ? ` — ${contact.contactEmail}` : ""}
          </span>
        ))}
      </section>

      <section className="preview-section">
        <h3>Sessions</h3>
        {data.sessions.length === 0 ? (
          <p className="preview-hint">No sessions yet.</p>
        ) : (
          data.sessions.map((entry) => (
            <button
              key={entry.sessionID}
              type="button"
              className="link-row"
              onClick={() => {
                navigate(`/prep/${engagement.engagementID}/${entry.sessionID}`);
              }}
            >
              {shortDate(entry.scheduledAt)} — {entry.sessionStatusLabel ?? "Session"} →
              open prep surface
            </button>
          ))
        )}
        {engagement.engagementStatus === "pendingAcceptance" ? (
          <p className="preview-hint">
            Pending acceptance: use <b>Accept Assignment</b> (Other Actions) — then send
            the intro email and schedule the first session.
          </p>
        ) : null}
      </section>

      <p className="preview-hint">
        Read-optimized preview — no edit controls. Edit via the Edit action, or a
        field's double-click, never this pane.
      </p>
    </div>
  );
}
