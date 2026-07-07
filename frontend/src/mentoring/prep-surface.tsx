/**
 * The session prep/conduct surface (WTK-177, REQ-081/082/079/089): a
 * data-dense refresh of the whole engagement before (and during) a
 * session. Per Doug's REQ-081 ruling the main column presents the FULL
 * ROLLUP — every session's notes and action items, newest first — plus the
 * engagement's history/stats and the conference Join affordance (REQ-079:
 * launching the link IS starting the conference; the app never hosts
 * video). The side column is the entry region: notes + action items ON the
 * session (REQ-082 — rich text, action items a bulleted rich-text field,
 * deliberately no task records), on the one rich-text control seam, sized
 * by the approved 3:2 fill split (entry_editors.PREP_ENTRY_EDITORS) so the
 * editors always fill the panel (REQ-089).
 */

import { type ReactElement, useEffect, useReducer, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { callApi } from "../api/envelope";
import { useEnvelope } from "../api/useEnvelope";
import { DeclinedNotice, EducateNotice, UnreachableNotice } from "../shell/educate";
import { refusalErrors } from "./compose-email";
import {
  type EngagementRollupPayload,
  type RollupSessionPayload,
  type SessionRecordPayload,
} from "./payloads";
import { isDirty, loadedEntry, pickPrepSession, reducePrepEntry } from "./prep-model";
import { RichTextEditor } from "./rich-text";
import { ScheduleSessionForm } from "./schedule-session";

function stamp(iso: string | null): string {
  return iso === null ? "—" : new Date(iso).toLocaleString();
}

export function PrepSurface(): ReactElement {
  const { engagementId, sessionId } = useParams();
  const { state, reload } = useEnvelope<EngagementRollupPayload>(
    `/engagements/${engagementId ?? ""}/rollup`,
  );

  if (state.phase === "loading") {
    return <p role="status">Loading the engagement…</p>;
  }
  if (state.phase === "declined") {
    return <DeclinedNotice errors={state.errors} />;
  }
  if (state.phase === "unreachable") {
    return <UnreachableNotice />;
  }
  return <LoadedPrep data={state.data} sessionId={sessionId} onDataChanged={reload} />;
}

function LoadedPrep({
  data,
  sessionId,
  onDataChanged,
}: {
  data: EngagementRollupPayload;
  sessionId: string | undefined;
  onDataChanged: () => void;
}): ReactElement {
  const navigate = useNavigate();
  const subject = pickPrepSession(data.sessions, sessionId);
  const engagement = data.engagement;

  return (
    <div className="prep-wrap" aria-label="Session prep">
      <div className="prep-main">
        <div className="prep-header">
          <h2>
            {engagement.engagementName ?? "Engagement"}
            {subject !== null ? ` — Session ${stamp(subject.scheduledAt)}` : ""}
          </h2>
          {engagement.engagementStatusLabel !== null ? (
            <span className="status-chip">{engagement.engagementStatusLabel}</span>
          ) : null}
          <button
            type="button"
            className="prep-back"
            onClick={() => {
              navigate(-1);
            }}
          >
            ← Back
          </button>
        </div>

        {/* Engagement history/stats (REQ-081), data-dense in one row. */}
        <div className="prep-stats">
          <span>
            Sessions held: <b>{data.stats.heldSessions}</b>
          </span>
          <span>
            First session: <b>{stamp(data.stats.firstSessionAt)}</b>
          </span>
          <span>
            Last session: <b>{stamp(data.stats.lastSessionAt)}</b>
          </span>
          <span>
            Next session: <b>{stamp(data.stats.nextSessionAt)}</b>
          </span>
          <span>
            Contact: <b>{engagement.primaryContactName ?? "—"}</b>
            {engagement.primaryContactEmail !== null
              ? ` · ${engagement.primaryContactEmail}`
              : ""}
          </span>
        </div>

        {/* REQ-079: the Join affordance — launching the link IS starting
            the conference; the app never hosts video. */}
        <div className="conf-bar">
          {subject?.conferenceLink != null ? (
            <>
              <a
                className="conf-join"
                href={subject.conferenceLink}
                target="_blank"
                rel="noreferrer"
              >
                ▶ Join Video Conference
              </a>
              <span className="conf-link">{subject.conferenceLink}</span>
            </>
          ) : (
            <span className="conf-link">
              No conference link yet — paste one below and save, or schedule with a
              link.
            </span>
          )}
        </div>

        {engagement.engagementSummary !== null ? (
          <section className="prep-card">
            <h3>Engagement summary</h3>
            <div dangerouslySetInnerHTML={{ __html: engagement.engagementSummary }} />
          </section>
        ) : null}

        {/* REQ-081: the FULL rollup — every session's notes + action items,
            newest first. No fill pass here: this surface's whole point is
            everything, scrolling as needed. */}
        <section className="prep-card">
          <h3>All notes &amp; action items across this engagement (newest first)</h3>
          {data.rollup.length === 0 ? (
            <p className="preview-hint">
              No sessions with notes yet. This panel fills with every session's notes
              and action items as the engagement runs.
            </p>
          ) : (
            data.rollup.map((entry) => (
              <div key={entry.sessionID} className="rollup-item">
                <div className="rollup-item-head">{stamp(entry.scheduledAt)}</div>
                {entry.sessionNotes !== null ? (
                  <div dangerouslySetInnerHTML={{ __html: entry.sessionNotes }} />
                ) : null}
                {entry.actionItems !== null ? (
                  <div dangerouslySetInnerHTML={{ __html: entry.actionItems }} />
                ) : null}
              </div>
            ))
          )}
        </section>
      </div>

      <div className="prep-side">
        {subject === null ? (
          <>
            <EducateNotice
              notice={{
                whatHappened: "This engagement has no sessions yet.",
                why: "Notes and action items are entered on a session, so one must exist first.",
                whatNext:
                  "Schedule the first session below; the entry area opens with it.",
              }}
            />
            <ScheduleSessionForm
              engagementId={engagement.engagementID}
              onScheduled={onDataChanged}
            />
          </>
        ) : (
          <PrepEntry
            key={subject.sessionID}
            engagementId={engagement.engagementID}
            session={subject}
            onSaved={onDataChanged}
          />
        )}
      </div>
    </div>
  );
}

/** The write side: this session's notes + action items (REQ-082/089). */
function PrepEntry({
  engagementId,
  session,
  onSaved,
}: {
  engagementId: string;
  session: RollupSessionPayload;
  onSaved: () => void;
}): ReactElement {
  const [entry, dispatch] = useReducer(reducePrepEntry, session, loadedEntry);
  const [scheduling, setScheduling] = useState(false);
  const dirty = isDirty(entry);

  // Unsaved notes survive an accidental close only through the browser's
  // own prompt — the one leave path the in-UI guard can't intercept.
  useEffect(() => {
    if (!dirty) {
      return;
    }
    const warn = (event: BeforeUnloadEvent): void => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
    };
  }, [dirty]);

  const save = (): void => {
    if (entry.saving) {
      return;
    }
    dispatch({ kind: "saveStarted" });
    callApi<SessionRecordPayload>(`/sessions/${entry.sessionID}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        rowVersion: entry.rowVersion,
        sessionNotes: entry.notes,
        actionItems: entry.actionItems,
      }),
    })
      .then(({ data }) => {
        dispatch({ kind: "saveSucceeded", rowVersion: data.rowVersion });
        // The rollup shows saved notes immediately (same-surface sync).
        onSaved();
      })
      .catch((failure: unknown) => {
        dispatch({ kind: "saveRefused", errors: refusalErrors(failure) });
      });
  };

  return (
    <div className="prep-entry" aria-label="This session's entry">
      <h3>This session — notes &amp; action items</h3>
      <p className="preview-hint">
        Entered during the call or shortly after. Formatting, lists, and links are kept;
        action items are a bulleted list, not task records.
      </p>
      {/* The approved 3:2 notes/action-items fill split
          (entry_editors.PREP_ENTRY_EDITORS) — REQ-089's no-idle-space rule. */}
      <RichTextEditor
        label="Session notes"
        initialHtml={entry.savedNotes}
        resetToken={entry.sessionID}
        fillWeight={3}
        onChange={(html) => {
          dispatch({ kind: "edited", field: "notes", value: html });
        }}
      />
      <RichTextEditor
        label="Action items"
        initialHtml={entry.savedActionItems}
        resetToken={entry.sessionID}
        fillWeight={2}
        onChange={(html) => {
          dispatch({ kind: "edited", field: "actionItems", value: html });
        }}
      />
      {entry.errors !== null ? <DeclinedNotice errors={entry.errors} /> : null}
      {entry.savedNotice !== null ? <p role="status">{entry.savedNotice}</p> : null}
      <div className="dialog-choices">
        <button type="button" disabled={entry.saving || !dirty} onClick={save}>
          Save Notes
        </button>
        <button
          type="button"
          onClick={() => {
            setScheduling((current) => !current);
          }}
        >
          Schedule Next Session
        </button>
      </div>
      {scheduling ? (
        <ScheduleSessionForm
          engagementId={engagementId}
          onScheduled={() => {
            setScheduling(false);
            onSaved();
          }}
        />
      ) : null}
    </div>
  );
}
