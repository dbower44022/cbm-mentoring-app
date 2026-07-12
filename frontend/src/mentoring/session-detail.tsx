/**
 * The session details surface (REQ-110, PI-015, DEC-098) — the approved
 * design: every fact appears exactly once. Identity band (date/time +
 * status chip) → context & actions bar (engagement link, client link,
 * conference control) → the attendee grid (one grid, role column keeps the
 * CRM's role split readable; per-cell and whole-grid copy) → notes and
 * action items side by side → the transcript section → the record-info
 * footer.
 *
 * The transcript is the record's longest text (Doug's 2026-07-11 ruling):
 * its TEXT loads on demand through its own read only when the state is
 * `attached`, scrolls within its own allotment, and carries its own find
 * (the browser's Ctrl+F cannot see lazily-loaded text). `expected` and
 * `unavailable` render the educate copy — the user-facing consequence of
 * meeting provenance, which is why no "meeting source" fact exists up top.
 *
 * Attendees are DERIVED (DEC-098): participation reads off the session
 * status pending the invited-vs-attended modeling ruling. Contact names
 * render as text — no contact read surface exists app-side yet; the company
 * link opens the company record through the one canonical opener.
 */

import {
  Fragment,
  type ReactElement,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { useEnvelope } from "../api/useEnvelope";
import { DeclinedNotice, EducateNotice, UnreachableNotice } from "../shell/educate";
import { popOutRecord } from "../windows/record";
import {
  type SessionAttendeePayload,
  type SessionDetailPayload,
  type SessionTranscriptPayload,
} from "./payloads";

function sessionMoment(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** The role column's display words — the payload speaks vocabulary names. */
function roleLabel(role: string): string {
  return role === "mentor" ? "Mentor" : "Client";
}

function participationLabel(participation: string): string {
  return participation === "attended" ? "Attended" : "Invited";
}

/** One TSV of the visible grid, headers first — pastes into Excel/Sheets. */
export function attendeeGridTsv(attendees: readonly SessionAttendeePayload[]): string {
  const header = ["Name", "Role", "Company", "Email", "Phone", "Status"].join("\t");
  const rows = attendees.map((entry) =>
    [
      entry.name,
      roleLabel(entry.role),
      entry.companyName ?? "",
      entry.email ?? "",
      entry.phone ?? "",
      participationLabel(entry.participation),
    ].join("\t"),
  );
  return [header, ...rows].join("\n");
}

/** The compose case: every attendee email, comma-separated, blanks dropped. */
export function attendeeEmails(attendees: readonly SessionAttendeePayload[]): string {
  return attendees
    .map((entry) => entry.email)
    .filter((email): email is string => email !== null && email !== "")
    .join(", ");
}

/** Case-insensitive match count — the find box's honest total. */
export function transcriptMatchCount(text: string, needle: string): number {
  if (needle === "") {
    return 0;
  }
  return text.toLowerCase().split(needle.toLowerCase()).length - 1;
}

function CopyButton({
  value,
  label,
  onCopied,
}: {
  value: string;
  label: string;
  onCopied: (label: string) => void;
}): ReactElement {
  return (
    <button
      type="button"
      className="copy-cell"
      aria-label={`Copy ${label}`}
      onClick={() => {
        void navigator.clipboard.writeText(value).then(() => {
          onCopied(label);
        });
      }}
    >
      ⧉
    </button>
  );
}

function AttendeeGrid({
  attendees,
  onCopied,
}: {
  attendees: readonly SessionAttendeePayload[];
  onCopied: (label: string) => void;
}): ReactElement {
  return (
    <section className="attendee-section" aria-label="Attendees">
      <div className="attendee-head">
        <h3>Attendees</h3>
        <span className="attendee-tools">
          <button
            type="button"
            onClick={() => {
              void navigator.clipboard
                .writeText(attendeeGridTsv(attendees))
                .then(() => {
                  onCopied("attendee grid");
                });
            }}
          >
            ⧉ Copy grid
          </button>
          <button
            type="button"
            onClick={() => {
              void navigator.clipboard.writeText(attendeeEmails(attendees)).then(() => {
                onCopied("attendee emails");
              });
            }}
          >
            ⧉ Copy emails
          </button>
        </span>
      </div>
      <table className="attendee-grid">
        <thead>
          <tr>
            <th>Name</th>
            <th>Role</th>
            <th>Company</th>
            <th>Email</th>
            <th>Phone</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {attendees.map((entry) => (
            <tr key={`${entry.role}:${entry.name}`}>
              <td>{entry.name}</td>
              <td>{roleLabel(entry.role)}</td>
              <td>
                {entry.companyRefID !== null ? (
                  <button
                    type="button"
                    className="link-row"
                    onClick={() => {
                      if (entry.companyRefID !== null) {
                        popOutRecord("crmCompanyRef", entry.companyRefID);
                      }
                    }}
                  >
                    {entry.companyName ?? "Company"}
                  </button>
                ) : (
                  (entry.companyName ?? "—")
                )}
              </td>
              <td>
                {entry.email ?? "—"}
                {entry.email !== null && (
                  <CopyButton value={entry.email} label="email" onCopied={onCopied} />
                )}
              </td>
              <td>
                {entry.phone ?? "—"}
                {entry.phone !== null && (
                  <CopyButton value={entry.phone} label="phone" onCopied={onCopied} />
                )}
              </td>
              <td>
                <span className={`att-chip att-${entry.participation}`}>
                  {participationLabel(entry.participation)}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

/** The attached-state transcript: on-demand text, own find, own scroll. */
function TranscriptText({
  sessionId,
  wordCount,
  source,
}: {
  sessionId: string;
  wordCount: number;
  source: string | null;
}): ReactElement {
  const { state } = useEnvelope<SessionTranscriptPayload>(
    `/sessions/${sessionId}/transcript`,
  );
  const [needle, setNeedle] = useState("");
  const bodyRef = useRef<HTMLDivElement>(null);

  const text = state.phase === "loaded" ? (state.data.transcriptText ?? "") : "";
  const matches = useMemo(() => transcriptMatchCount(text, needle), [text, needle]);

  // Jump to the first hit as the needle changes — find must move the view,
  // not just paint marks the user has to hunt for.
  useEffect(() => {
    if (needle !== "" && matches > 0) {
      bodyRef.current?.querySelector("mark")?.scrollIntoView({ block: "nearest" });
    }
  }, [needle, matches]);

  const provenance =
    source === "platform" ? "From the meeting platform" : "Added manually";

  return (
    <>
      <div className="transcript-head">
        <h3>Transcript</h3>
        <span className="transcript-prov">
          {provenance} · {wordCount.toLocaleString()} words
        </span>
        <input
          type="search"
          className="transcript-find"
          placeholder="Find in transcript…"
          aria-label="Find in transcript"
          value={needle}
          onChange={(event) => {
            setNeedle(event.target.value);
          }}
        />
        {needle !== "" && (
          <span className="transcript-matches" role="status">
            {matches} match{matches === 1 ? "" : "es"}
          </span>
        )}
      </div>
      {state.phase === "loading" && <p role="status">Loading transcript…</p>}
      {state.phase === "declined" && <DeclinedNotice errors={state.errors} />}
      {state.phase === "unreachable" && <UnreachableNotice />}
      {state.phase === "loaded" && (
        <div className="transcript-body" ref={bodyRef}>
          {needle === ""
            ? text
            : text
                .split(
                  new RegExp(
                    `(${needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`,
                    "gi",
                  ),
                )
                .map((piece, index) =>
                  index % 2 === 1 ? (
                    <mark key={index}>{piece}</mark>
                  ) : (
                    <Fragment key={index}>{piece}</Fragment>
                  ),
                )}
        </div>
      )}
    </>
  );
}

/** The transcript section's three states — the educate copy Doug approved. */
function TranscriptSection({
  sessionId,
  transcript,
}: {
  sessionId: string;
  transcript: SessionDetailPayload["transcript"];
}): ReactElement {
  if (transcript.state === "attached") {
    return (
      <section className="transcript-section" aria-label="Transcript">
        <TranscriptText
          sessionId={sessionId}
          wordCount={transcript.wordCount}
          source={transcript.source}
        />
      </section>
    );
  }
  if (transcript.state === "expected") {
    return (
      <section className="transcript-section" aria-label="Transcript">
        <div className="transcript-head">
          <h3>Transcript</h3>
        </div>
        <EducateNotice
          notice={{
            whatHappened: "The transcript is not here yet.",
            why: "The meeting platform has not produced it — retrieval retries automatically.",
            whatNext: "No action is needed; it attaches on its own once ready.",
          }}
        />
      </section>
    );
  }
  return (
    <section className="transcript-section" aria-label="Transcript">
      <div className="transcript-head">
        <h3>Transcript</h3>
      </div>
      <EducateNotice
        notice={{
          whatHappened:
            "No transcript is attached — it can't be retrieved automatically.",
          why: "This session's meeting link was added by hand, so the app can't ask the meeting platform for its transcript.",
          whatNext:
            "Paste or upload the transcript on the session to attach it — the AI draft summary becomes available once a transcript exists.",
        }}
      />
    </section>
  );
}

export function SessionDetailPreview({
  recordId,
  refreshToken = null,
}: {
  recordId: string;
  refreshToken?: unknown;
}): ReactElement {
  const { state } = useEnvelope<SessionDetailPayload>(
    `/sessions/${recordId}/detail`,
    refreshToken,
  );
  const [copied, setCopied] = useState<string | null>(null);
  const [noLink, setNoLink] = useState(false);

  // The "Copied" note is a moment, not a mode — it clears itself.
  useEffect(() => {
    if (copied === null) {
      return;
    }
    const timer = setTimeout(() => {
      setCopied(null);
    }, 2000);
    return () => {
      clearTimeout(timer);
    };
  }, [copied]);

  switch (state.phase) {
    case "loading":
      return <p role="status">Loading session…</p>;
    case "declined":
      return <DeclinedNotice errors={state.errors} />;
    case "unreachable":
      return <UnreachableNotice />;
    case "loaded":
      break;
  }
  const detail = state.data;
  const record = detail.session;

  return (
    <div className="session-detail" aria-label="Session details">
      <div className="session-title">
        <h2>Session — {sessionMoment(record.scheduledAt)}</h2>
        {record.sessionStatusLabel !== null && (
          <span className="status-chip">{record.sessionStatusLabel}</span>
        )}
      </div>

      <div className="session-context">
        <span>
          <span className="ctx-label">Engagement</span>
          <button
            type="button"
            className="link-row"
            onClick={() => {
              popOutRecord("engagement", detail.engagement.engagementID);
            }}
          >
            {detail.engagement.engagementName ?? "Engagement"}
          </button>
        </span>
        {detail.client !== null && (
          <span>
            <span className="ctx-label">Client</span>
            <button
              type="button"
              className="link-row"
              onClick={() => {
                if (detail.client !== null) {
                  popOutRecord("client", detail.client.clientID);
                }
              }}
            >
              {detail.client.crmCompanyID ?? "Client record"}
            </button>
          </span>
        )}
        {record.conferenceLink !== null ? (
          <a
            className="join-conference"
            href={record.conferenceLink}
            target="_blank"
            rel="noreferrer"
          >
            Join conference
          </a>
        ) : (
          <button
            type="button"
            className="join-conference"
            onClick={() => {
              setNoLink(true);
            }}
          >
            Join conference
          </button>
        )}
      </div>
      {noLink && (
        <EducateNotice
          notice={{
            whatHappened: "There is no conference to join yet.",
            why: "This session has no conference link on it.",
            whatNext:
              "Add a link on the session (edit its Conference link field), or reschedule through Schedule Session to book an org meeting.",
          }}
        />
      )}

      <AttendeeGrid attendees={detail.attendees} onCopied={setCopied} />
      {copied !== null && (
        <p className="copy-confirm" role="status">
          Copied {copied} to the clipboard.
        </p>
      )}

      <div className="session-longform">
        <section aria-label="Session notes">
          <h3>Session notes</h3>
          {record.sessionNotes !== null ? (
            // Clean semantic HTML from the one rich-text control (REQ-090);
            // rendering it as HTML is the read side of that contract.
            <div dangerouslySetInnerHTML={{ __html: record.sessionNotes }} />
          ) : (
            <p className="preview-hint">No notes on this session yet.</p>
          )}
        </section>
        <section aria-label="Action items">
          <h3>Action items</h3>
          {record.actionItems !== null ? (
            <div dangerouslySetInnerHTML={{ __html: record.actionItems }} />
          ) : (
            <p className="preview-hint">No action items on this session yet.</p>
          )}
          {record.draftSummary !== null && (
            <p className="draft-flag">
              A draft summary from the transcript is awaiting your review on the prep
              surface — it is a proposal, nothing is applied until you accept it.
            </p>
          )}
        </section>
      </div>

      <TranscriptSection sessionId={record.sessionID} transcript={detail.transcript} />

      <p className="record-info">
        <span>session {record.sessionID}</span>
        <span>version {record.rowVersion}</span>
      </p>
    </div>
  );
}
