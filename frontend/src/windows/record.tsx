/**
 * Record preview & pop-out windows (REQ-012, WTK-021's reference behavior).
 * `RecordPreview` renders one record read-optimized from
 * `GET /records/{entityType}/{recordId}/preview`: no edit controls ever (the
 * pane declares editing's two paths; it never hosts them), and a
 * soft-deleted record renders WITH its educate notice — a pinned window must
 * explain, never blank. `RecordWindow` is the `/records/...` route a pop-out
 * loads: a REAL browser window pinned to its record, standard header minus
 * navigation. `popOutRecord` is the one canonical opener grids and previews
 * call.
 */

import { type ReactElement } from "react";
import { Link, useParams } from "react-router-dom";

import { type RecordPreviewPayload } from "../api/payloads";
import { useEnvelope } from "../api/useEnvelope";
import { NotificationBell } from "../shell/bell";
import { DeclinedNotice, EducateNotice, UnreachableNotice } from "../shell/educate";

/** Display rendering for one flat record value; identity stays server-side. */
export function formatFieldValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (typeof value === "string" || typeof value === "number") {
    return String(value);
  }
  return JSON.stringify(value);
}

/** One pop-out window per record: the window NAME is the record identity. */
export function popOutWindowName(entityType: string, recordId: string): string {
  return `record:${entityType}:${recordId}`;
}

/**
 * Open (or raise) the pop-out for one record. Inputs: the record identity.
 * Output: none — the effect is a real browser window (multi-monitor, and it
 * survives main-window close by construction). Re-invoking for a record
 * already open reuses the named window and focuses it — the layout
 * standard's switch-to-that-window rule — instead of stacking a duplicate.
 * Failure mode: a popup blocker yields null; the caller's link still works
 * in-window, so nothing is lost silently.
 */
export function popOutRecord(entityType: string, recordId: string): void {
  const opened = window.open(
    `/records/${entityType}/${recordId}`,
    popOutWindowName(entityType, recordId),
    "popup=yes,width=1000,height=800",
  );
  opened?.focus();
}

/**
 * The Edit action's opener (REQ-032): the record's pinned window, at the
 * full-screen edit route. SAME window name as `popOutRecord` — one window
 * per record holds (layout standard's switch-to-that-window rule), and an
 * open read window becomes the edit form rather than stacking a duplicate.
 */
export function popOutRecordEdit(entityType: string, recordId: string): void {
  const opened = window.open(
    `/records/${entityType}/${recordId}/edit`,
    popOutWindowName(entityType, recordId),
    "popup=yes,width=1000,height=800",
  );
  opened?.focus();
}

export function RecordPreview({
  entityType,
  recordId,
}: {
  entityType: string;
  recordId: string;
}): ReactElement {
  const { state } = useEnvelope<RecordPreviewPayload>(
    `/records/${entityType}/${recordId}/preview`,
  );

  switch (state.phase) {
    case "loading":
      return <p>Loading record…</p>;
    case "declined":
      return <DeclinedNotice errors={state.errors} />;
    case "unreachable":
      return <UnreachableNotice />;
    case "loaded":
      return (
        <article aria-label="Record preview">
          {state.data.notice !== null && <EducateNotice notice={state.data.notice} />}
          <dl>
            {Object.entries(state.data.record).map(([fieldName, value]) => (
              <div key={fieldName}>
                <dt>{fieldName}</dt>
                <dd>{formatFieldValue(value)}</dd>
              </div>
            ))}
          </dl>
        </article>
      );
  }
}

/** The route a pop-out window loads; params come from `/records/:entityType/:recordId`. */
export function RecordWindow(): ReactElement {
  const { entityType, recordId } = useParams();
  if (entityType === undefined || recordId === undefined) {
    // Unreachable under the declared route; typed as optional by the router.
    return (
      <EducateNotice
        notice={{
          whatHappened: "This record window has nothing to show.",
          why: "The address does not name a record.",
          whatNext: "Open a record from a grid or a link that points at one.",
        }}
      />
    );
  }
  return (
    <div>
      {/* The standard header minus navigation: a record window, not a panel
          host (popOutFrame.hasNavigation is false). Help and the account
          menu join when the shell-header slice renders them app-wide. */}
      <header>
        <span>CBM Mentoring</span>
        {/* The pane's first edit path (REQ-012 editPaths[0], REQ-032): the
            Edit action, hosted by the window frame — never the preview. */}
        <Link to={`/records/${entityType}/${recordId}/edit`} className="edit-action">
          Edit
        </Link>
        <NotificationBell />
      </header>
      <RecordPreview entityType={entityType} recordId={recordId} />
    </div>
  );
}
