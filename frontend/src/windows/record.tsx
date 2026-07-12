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

import { type ReactElement, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  type EducatePayload,
  type FormFieldPayload,
  type RecordPreviewPayload,
} from "../api/payloads";
import { useEnvelope } from "../api/useEnvelope";
import { FieldEditWindow } from "../forms/field-edit-window";
// Module cycle with the seam (seam → domain previews → this file's opener)
// is deliberate and safe: the opener is a hoisted function declaration the
// previews call from event handlers, long after both modules evaluate.
import { previewRendererForEntityType } from "../grid/preview-seam";
import { NotificationBell } from "../shell/bell";
import { DeclinedNotice, EducateNotice, UnreachableNotice } from "../shell/educate";

/** The schema payload's field entry, promoted to the form vocabulary. */
type SchemaFieldSpec = Omit<FormFieldPayload, "editable" | "readOnly" | "help">;

interface EntitySchemaPayload {
  entityType: string;
  fields: SchemaFieldSpec[];
}

/** One explanation per field, two gestures (readonly_fields wording). */
function cannotEditMessage(fieldName: string, computed: boolean): EducatePayload {
  if (computed) {
    return {
      whatHappened: `'${fieldName}' can't be edited.`,
      why: "Its value is calculated automatically from other information on the record.",
      whatNext:
        "Edit the fields it is calculated from — this value updates on its own.",
    };
  }
  return {
    whatHappened: `'${fieldName}' can't be edited.`,
    why: "This is a system field; it is maintained automatically.",
    whatNext:
      "No action is needed — the system keeps it up to date as the record changes.",
  };
}

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

/** Open the create form for one entity in a fresh window (the New action).
 * Unlike a record window there is nothing to pin yet, so every New opens
 * its own window — several creates may be in flight at once. */
export function popOutRecordCreate(entityType: string): void {
  const opened = window.open(
    `/records/${entityType}/new`,
    "_blank",
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
  const { state, reload } = useEnvelope<RecordPreviewPayload>(
    `/records/${entityType}/${recordId}/preview`,
  );
  // The double-click gesture's field settings (REQ-035): the same schema
  // read every form renders from. A declined/unreachable schema degrades to
  // the refusal path — the preview itself never depends on it.
  const { state: schemaState } = useEnvelope<EntitySchemaPayload>(
    `/schema/${entityType}`,
  );
  const [editing, setEditing] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<EducatePayload | null>(null);

  const openFieldEditor = (fieldName: string): void => {
    // The pane's second edit path (editPaths[1]): double-click opens the
    // small per-field window, or explains why the field has no editor —
    // never a silent nothing (REQ-039's words, both gestures).
    const spec =
      schemaState.phase === "loaded"
        ? schemaState.data.fields.find((entry) => entry.fieldName === fieldName)
        : undefined;
    if (spec === undefined) {
      setRefusal(cannotEditMessage(fieldName, false));
      return;
    }
    const hints = spec.visibilityHints;
    if (hints !== null && Boolean(hints.computed)) {
      setRefusal(cannotEditMessage(spec.fieldLabel, true));
      return;
    }
    setEditing(fieldName);
  };

  switch (state.phase) {
    case "loading":
      return <p>Loading record…</p>;
    case "declined":
      return <DeclinedNotice errors={state.errors} />;
    case "unreachable":
      return <UnreachableNotice />;
    case "loaded": {
      const record = state.data.record;
      const editingSpec =
        editing !== null && schemaState.phase === "loaded"
          ? schemaState.data.fields.find((entry) => entry.fieldName === editing)
          : undefined;
      return (
        <article aria-label="Record preview">
          {state.data.notice !== null && <EducateNotice notice={state.data.notice} />}
          <dl>
            {Object.entries(record).map(([fieldName, value]) => (
              <div
                key={fieldName}
                onDoubleClick={() => {
                  openFieldEditor(fieldName);
                }}
              >
                <dt>{fieldName}</dt>
                <dd>{formatFieldValue(value)}</dd>
              </div>
            ))}
          </dl>
          {refusal !== null && (
            <div
              role="dialog"
              aria-label="Why this field is not editable"
              className="field-explain"
            >
              <EducateNotice notice={refusal} />
              <button
                type="button"
                tabIndex={-1}
                onClick={() => {
                  setRefusal(null);
                }}
              >
                Got it
              </button>
            </div>
          )}
          {editingSpec !== undefined && (
            <FieldEditWindow
              entityType={entityType}
              recordId={recordId}
              field={{ ...editingSpec, editable: true, readOnly: null, help: null }}
              baseValue={record[editingSpec.fieldName]}
              rowVersion={Number(record.rowVersion)}
              onSaved={() => {
                // The single-field write landed: the window closes and the
                // preview re-reads its one content answer.
                setEditing(null);
                reload();
              }}
              onClose={() => {
                setEditing(null);
              }}
            />
          )}
        </article>
      );
    }
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
  // The seam's pop-out decision (REQ-110): a domain preview owns its entity
  // type; the generic flat pane stays the fallback for everything unmapped.
  const DomainPreview = previewRendererForEntityType(entityType);
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
      {DomainPreview !== null ? (
        <DomainPreview recordId={recordId} />
      ) : (
        <RecordPreview entityType={entityType} recordId={recordId} />
      )}
    </div>
  );
}
