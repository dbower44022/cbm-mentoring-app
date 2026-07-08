/**
 * The full-screen edit form (REQ-032/033/038/039/040 — REL-004 block 1).
 * Renders the `/records/{entityType}/{recordId}/edit-form` view-model
 * verbatim: the Python modules (`ui/edit_form.py`, `ui/readonly_fields.py`,
 * `ui/form_keyboard.py`, `ui/field_help.py`, `api/form_validation.py`,
 * `api/edit_safety.py`) are the behavior contract; this file only puts it on
 * screen:
 *
 * - Value lifecycle mirrors `EditForm`: dirty is a VALUE comparison against
 *   the loaded originals, Cancel reverts and stays open, Save PATCHes only
 *   the changed fields against `rowVersion`, a landed save rebases.
 * - Validation is field-settings-driven (REQ-033): required marker from
 *   `requiredFlag`, per-field check on focus exit, a full sweep at save that
 *   focuses the first problem, messages inline at the offending field; a
 *   422's `errors[]` re-places server entries the same way.
 * - A 409 routes through the `resolve_concurrent_save_conflict` algorithm:
 *   agreement pruning, invisible auto-retry when the other save touched
 *   different fields, the manual walk-through only on a true overlap.
 * - Keyboard (REQ-038): Ctrl+S saves, Escape requests leave through the
 *   dirty guard, Enter never submits, Tab cycles editable fields only
 *   (Save/Cancel included in the exclusion — Ctrl+S is the keyboard's path).
 * - Read-only fields render in place with click-to-explain (REQ-039); help
 *   markers render only where field settings carry text (REQ-040).
 */

import {
  type KeyboardEvent,
  type ReactElement,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { type ApiError, callApi, EnvelopeError } from "../api/envelope";
import {
  type EditFormPayload,
  type EducatePayload,
  type FormFieldPayload,
} from "../api/payloads";
import { RichTextEditor } from "../mentoring/rich-text";
import { EducateNotice } from "../shell/educate";
import { formatFieldValue } from "../windows/record";
import { LookupControl } from "./lookup-control";

/** The DB-R2 exemption set (api/records.py STRUCTURAL_FIELDS) — never counts
 * as another save's business change in conflict resolution. */
const STRUCTURAL_FIELDS = new Set([
  "createdAt",
  "createdBy",
  "modifiedAt",
  "modifiedBy",
  "deletedAt",
  "deletedBy",
  "rowVersion",
  "customAttributes",
]);

/** edit_safety.AUTO_RETRY_LIMIT — past this, a stale save always walks through. */
const AUTO_RETRY_LIMIT = 3;

/** A control's display text for a wire value (string/number verbatim). */
function controlText(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number") {
    return String(value);
  }
  return "";
}

/** form_validation.normalized_input: blank text is NO value. */
export function blankToNull(value: unknown): unknown {
  if (typeof value === "string" && value.trim() === "") {
    return null;
  }
  return value;
}

/**
 * The client half of the shared validator (write_engine.validate_value) for
 * the checks a control can violate before the server sees the payload:
 * required-ness and choice membership. Everything else (type gates the
 * controls already enforce, registry rules) stays the server's save-time
 * truth — a clean sweep here never claims server validity, it only stops the
 * round trip the settings alone can refuse. Messages repeat the server's
 * words so on-exit and save-time speak one voice.
 */
export function validateOnExit(field: FormFieldPayload, raw: unknown): string | null {
  const value = blankToNull(raw);
  if (value === null || value === undefined) {
    return field.requiredFlag ? "This field is required." : null;
  }
  if (field.fieldType === "choice" && field.optionSet !== null) {
    const match = field.optionSet.optionValues.find(
      (option) => option.optionValueID === value,
    );
    if (match === undefined) {
      return "Not a value in this list.";
    }
    if (!match.activeFlag) {
      return "This value has been retired.";
    }
  }
  return null;
}

/** One dirty diff: the minimal PATCH payload (edit_form.request_save). */
export function dirtyChanges(
  original: Record<string, unknown>,
  current: Record<string, unknown>,
): Record<string, unknown> {
  const changes: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(current)) {
    if (value !== original[name]) {
      changes[name] = value;
    }
  }
  return changes;
}

/** The resolve_concurrent_save_conflict outcomes, mirrored (edit_safety.py). */
export type ConflictResolution =
  | { kind: "retry"; changes: Record<string, unknown>; rowVersion: number }
  | { kind: "alreadyCurrent"; rowVersion: number }
  | {
      kind: "manualMerge";
      conflicts: { fieldName: string; yourValue: unknown; theirValue: unknown }[];
      cleanChanges: Record<string, unknown>;
      rowVersion: number;
    };

export function resolveStaleSave(
  dirty: Record<string, unknown>,
  loaded: Record<string, unknown>,
  current: Record<string, unknown>,
  attempt: number,
): ConflictResolution {
  const businessFields = new Set(
    [...Object.keys(loaded), ...Object.keys(current)].filter(
      (name) => !STRUCTURAL_FIELDS.has(name),
    ),
  );
  const theirChanged = new Set(
    [...businessFields].filter((name) => loaded[name] !== current[name]),
  );
  // Agreement pruning: a value the other save already wrote is neither a
  // conflict nor worth re-sending.
  const stillDirty = Object.fromEntries(
    Object.entries(dirty).filter(([name, value]) => value !== current[name]),
  );
  const conflicted = Object.keys(stillDirty)
    .filter((name) => theirChanged.has(name))
    .sort();
  const rowVersion = Number(current.rowVersion);
  if (Object.keys(stillDirty).length === 0) {
    return { kind: "alreadyCurrent", rowVersion };
  }
  if (conflicted.length === 0 && attempt <= AUTO_RETRY_LIMIT) {
    return { kind: "retry", changes: stillDirty, rowVersion };
  }
  return {
    kind: "manualMerge",
    conflicts: conflicted.map((fieldName) => ({
      fieldName,
      yourValue: stillDirty[fieldName],
      theirValue: current[fieldName],
    })),
    cleanChanges: Object.fromEntries(
      Object.entries(stillDirty).filter(([name]) => !conflicted.includes(name)),
    ),
    rowVersion,
  };
}

/** edit_form.leave_warning, verbatim wording. */
export function leaveWarning(dirtyLabels: string[]): EducatePayload {
  const fields = dirtyLabels.length === 1 ? "field has" : "fields have";
  return {
    whatHappened: "This page has unsaved changes.",
    why: `${String(dirtyLabels.length)} ${fields} been changed but not saved: ${dirtyLabels.join(", ")}.`,
    whatNext: "Save to keep the changes, or discard them to leave without saving.",
  };
}

// --- Smart input (REQ-034): the thin client over /form-input — the parsers
// stay server-side (DB-S13 one canonical home), the form only applies fills.

/** The auto-formatted types (form_input._FORMATTERS' vocabulary). */
export const FORMATTED_TYPES = new Set(["phone", "email", "website", "postalCode"]);

/** Composite types that accept a smart paste (form_input.PASTE_RESOLVERS). */
export const PASTE_RESOLVABLE_TYPES = new Set(["personName", "address"]);

/** Auto-format one typed value on focus exit — convenience, never a gate:
 * an unreachable formatter returns the value as typed. */
export async function autoFormatValue(
  fieldType: string,
  value: string,
): Promise<string> {
  try {
    const result = await callApi<{ value: string }>("/form-input/format", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fieldType, value }),
    });
    return result.data.value;
  } catch {
    return value;
  }
}

/** Resolve pasted free text into components; a failed resolve keeps the
 * whole paste as remainder — the paste is NEVER blocked. */
export async function resolvePasteText(
  fieldType: string,
  text: string,
): Promise<{ components: Record<string, string>; remainder: string }> {
  try {
    const result = await callApi<{
      components: Record<string, string>;
      remainder: string;
    }>("/form-input/resolve-paste", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fieldType, text }),
    });
    return result.data;
  } catch {
    return { components: {}, remainder: text };
  }
}

/** City/state for a postal code — null means unknown, not invalid. */
export async function postalFill(
  postalCode: string,
): Promise<{ cityName: string; stateCode: string } | null> {
  try {
    const result = await callApi<{
      fill: { cityName: string; stateCode: string } | null;
    }>(`/form-input/postal-autofill?postal_code=${encodeURIComponent(postalCode)}`);
    return result.data.fill;
  } catch {
    return null;
  }
}

/** The label block every field variant shares: asterisk + help marker (REQ-033/040). */
export function FieldLabel({
  field,
  htmlFor,
}: {
  field: FormFieldPayload;
  htmlFor?: string;
}): ReactElement {
  return (
    <label className="form-field-label" htmlFor={htmlFor}>
      <span>
        {field.fieldLabel}
        {field.requiredFlag && <span className="required-marker"> *</span>}
      </span>
      {field.help !== null && (
        // The subtle affordance (REQ-040): marker on the label, text on
        // hover/focus, never a persistent paragraph, no Tab stop.
        <span className="field-help" aria-label={`About ${field.fieldLabel}`}>
          <span className="field-help-marker" aria-hidden="true">
            ⓘ
          </span>
          <span className="field-help-text" role="tooltip">
            {field.help.helpText}
          </span>
        </span>
      )}
    </label>
  );
}

/** One editable control, dispatched on the registry fieldType. */
export function FieldControl({
  entityType,
  field,
  value,
  invalid,
  onChange,
  onExit,
  onSmartPaste,
}: {
  entityType: string;
  field: FormFieldPayload;
  value: unknown;
  invalid: boolean;
  onChange: (value: unknown) => void;
  onExit: () => void;
  /** Composite-field paste (REQ-034): the form resolves and fills; absent
   * means the host has no cross-field fill (paste lands as typed). */
  onSmartPaste?: (text: string) => void;
}): ReactElement {
  const controlId = `field-${field.fieldName}`;
  const shared = {
    id: controlId,
    "aria-invalid": invalid || undefined,
    onBlur: onExit,
  };
  if (field.fieldType === "reference") {
    // The one relationship control (REQ-036), registry-driven — never a
    // per-form widget choice.
    return (
      <LookupControl
        entityType={entityType}
        field={field}
        value={value}
        invalid={invalid}
        onChange={onChange}
        onExit={onExit}
      />
    );
  }
  if (field.fieldType === "boolean") {
    return (
      <input
        {...shared}
        type="checkbox"
        checked={value === true}
        onChange={(event) => {
          onChange(event.target.checked);
        }}
      />
    );
  }
  if (field.fieldType === "choice" && field.optionSet !== null) {
    const options = [...field.optionSet.optionValues].sort(
      (a, b) => a.optionValueSortOrder - b.optionValueSortOrder,
    );
    const held = typeof value === "string" ? value : "";
    return (
      <select
        {...shared}
        value={held}
        onChange={(event) => {
          onChange(event.target.value === "" ? null : event.target.value);
        }}
      >
        <option value="">—</option>
        {options
          // Retired values are hidden from NEW entry but a historical record
          // holding one must still render it (schema router's serving rule).
          .filter((option) => option.activeFlag || option.optionValueID === held)
          .map((option) => (
            <option key={option.optionValueID} value={option.optionValueID}>
              {option.optionValueLabel}
              {option.activeFlag ? "" : " (retired)"}
            </option>
          ))}
      </select>
    );
  }
  if (field.fieldType === "richText") {
    return (
      <RichTextEditor
        label={field.fieldLabel}
        initialHtml={typeof value === "string" ? value : ""}
        resetToken={field.fieldName}
        onChange={onChange}
        fillWeight={1}
      />
    );
  }
  if (field.fieldType === "number") {
    return (
      <input
        {...shared}
        type="text"
        inputMode="decimal"
        value={controlText(value)}
        onChange={(event) => {
          const text = event.target.value;
          const numeric = Number(text);
          // A parsable number travels typed; anything else stays text for
          // the server's type gate to refuse inline (never blocked here).
          onChange(text.trim() !== "" && !Number.isNaN(numeric) ? numeric : text);
        }}
      />
    );
  }
  if (field.fieldType === "date" || field.fieldType === "datetime") {
    return (
      <input
        {...shared}
        type={field.fieldType === "date" ? "date" : "datetime-local"}
        value={typeof value === "string" ? value : ""}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
    );
  }
  return (
    <input
      {...shared}
      type="text"
      data-field-type={field.fieldType}
      value={controlText(value)}
      onChange={(event) => {
        onChange(event.target.value);
      }}
      onPaste={
        onSmartPaste !== undefined && PASTE_RESOLVABLE_TYPES.has(field.fieldType)
          ? (event) => {
              event.preventDefault();
              onSmartPaste(event.clipboardData.getData("text"));
            }
          : undefined
      }
    />
  );
}

export interface EditFormScreenProps {
  entityType: string;
  recordId: string;
  payload: EditFormPayload;
  /** Leave was requested and the guard allowed (or the user discarded). */
  onLeave: () => void;
}

export function EditFormScreen({
  entityType,
  recordId,
  payload,
  onLeave,
}: EditFormScreenProps): ReactElement {
  const editableFields = useMemo(
    () => payload.fields.filter((field) => field.editable),
    [payload.fields],
  );
  const fieldsByName = useMemo(
    () => new Map(payload.fields.map((field) => [field.fieldName, field])),
    [payload.fields],
  );

  const seed = (record: Record<string, unknown>): Record<string, unknown> =>
    Object.fromEntries(
      editableFields.map((field) => [field.fieldName, record[field.fieldName] ?? null]),
    );

  // The EditForm value lifecycle: originals, currents, the PATCH base.
  const [original, setOriginal] = useState(() => seed(payload.record));
  const [values, setValues] = useState(original);
  const [rowVersion, setRowVersion] = useState(() => Number(payload.record.rowVersion));
  const [loadedRecord, setLoadedRecord] = useState(payload.record);

  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formErrors, setFormErrors] = useState<ApiError[]>([]);
  const [explain, setExplain] = useState<EducatePayload | null>(null);
  const [guard, setGuard] = useState<EducatePayload | null>(null);
  const [merge, setMerge] = useState<Extract<
    ConflictResolution,
    { kind: "manualMerge" }
  > | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const containerRef = useRef<HTMLFormElement>(null);

  const dirty = dirtyChanges(
    Object.fromEntries(
      Object.entries(original).map(([name, value]) => [name, blankToNull(value)]),
    ),
    Object.fromEntries(
      Object.entries(values).map(([name, value]) => [name, blankToNull(value)]),
    ),
  );
  const isDirty = Object.keys(dirty).length > 0;

  // Initial focus: the first editable field (REQ-038), server-resolved.
  useEffect(() => {
    if (payload.initialFocusField !== null) {
      focusField(payload.initialFocusField);
    }
    // The payload is this window's one load — first render only.
  }, []);

  // The browser-level half of the leave guard: closing the window/tab with
  // unsaved changes warns (the in-app half is requestLeave below).
  useEffect(() => {
    if (!isDirty) {
      return;
    }
    const warn = (event: BeforeUnloadEvent): void => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
    };
  }, [isDirty]);

  const focusField = (fieldName: string): void => {
    const host = containerRef.current;
    // Attribute selector: field names are registry identifiers, but an
    // attribute match needs no CSS-identifier escaping (jsdom included).
    const control = host?.querySelector<HTMLElement>(`[id="field-${fieldName}"]`);
    control?.focus();
  };

  const setValue = (fieldName: string, value: unknown): void => {
    setValues((current) => ({ ...current, [fieldName]: value }));
  };

  const fillEmptyFields = (fill: Record<string, string>): void => {
    // The REQ-034 auto-fill rule: EMPTY controls only — a user-typed value
    // is never overwritten.
    setValues((current) => {
      const next = { ...current };
      for (const [name, filled] of Object.entries(fill)) {
        if (name in next && blankToNull(next[name]) === null) {
          next[name] = filled;
        }
      }
      return next;
    });
  };

  const exitField = async (field: FormFieldPayload): Promise<void> => {
    let value = values[field.fieldName];
    // Auto-format runs BEFORE validation sees the value (REQ-034): what the
    // user sees, what validates, and what the payload carries are the same
    // string. Convenience, never a gate.
    if (
      FORMATTED_TYPES.has(field.fieldType) &&
      typeof value === "string" &&
      value.trim() !== ""
    ) {
      const formatted = await autoFormatValue(field.fieldType, value);
      if (formatted !== value) {
        setValue(field.fieldName, formatted);
        value = formatted;
      }
    }
    // Per-field validation on exit (REQ-033): this field only.
    const problem = validateOnExit(field, value);
    setFieldErrors((current) => {
      const next = Object.fromEntries(
        Object.entries(current).filter(([name]) => name !== field.fieldName),
      );
      if (problem !== null) {
        next[field.fieldName] = problem;
      }
      return next;
    });
    // A postal code landing fills empty city/state (the one auto-fill path).
    if (field.fieldType === "postalCode" && typeof value === "string" && value !== "") {
      const fill = await postalFill(value);
      if (fill !== null) {
        fillEmptyFields(fill);
      }
    }
  };

  const smartPaste = async (field: FormFieldPayload, text: string): Promise<void> => {
    // Composite paste (REQ-034): confident components fill in, the
    // unresolved remainder stays VISIBLE in the pasted-into control, and
    // the paste is never blocked.
    const { components, remainder } = await resolvePasteText(field.fieldType, text);
    setValues((current) => {
      const next = { ...current, [field.fieldName]: remainder };
      for (const [name, filled] of Object.entries(components)) {
        if (name in next) {
          next[name] = filled;
        }
      }
      return next;
    });
    const postal = components.postalCode;
    if (postal !== undefined && postal !== "") {
      const fill = await postalFill(postal);
      if (fill !== null) {
        fillEmptyFields(fill);
      }
    }
  };

  const placeServerErrors = (errors: ApiError[]): void => {
    const inline: Record<string, string> = {};
    const formLevel: ApiError[] = [];
    for (const error of errors) {
      if (error.fieldName !== null && fieldsByName.has(error.fieldName)) {
        inline[error.fieldName] = error.message;
      } else {
        // An error the user cannot see is a save that silently fails —
        // nothing is dropped (form_validation.place_save_errors).
        formLevel.push(error);
      }
    }
    setFieldErrors(inline);
    setFormErrors(formLevel);
    const first = editableFields.find((field) => field.fieldName in inline);
    if (first !== undefined) {
      focusField(first.fieldName);
    }
  };

  const rebase = (record: Record<string, unknown>): void => {
    const next = seed(record);
    setOriginal(next);
    setValues(next);
    setLoadedRecord(record);
    setRowVersion(Number(record.rowVersion));
  };

  const patch = async (
    changes: Record<string, unknown>,
    version: number,
    attempt: number,
  ): Promise<void> => {
    try {
      const result = await callApi<{ record: Record<string, unknown> }>(
        `/records/${entityType}/${recordId}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...changes, rowVersion: version }),
        },
      );
      // The PATCH landed: rebase — the form stays open (saving ≠ closing).
      rebase(result.data.record);
      setFieldErrors({});
      setFormErrors([]);
      setMerge(null);
      setSavedAt(new Date().toLocaleTimeString());
    } catch (failure: unknown) {
      if (!(failure instanceof EnvelopeError)) {
        setFormErrors([
          {
            fieldName: null,
            code: "unreachable",
            message:
              "The save didn't reach the server. Check your connection and try again.",
          },
        ]);
        return;
      }
      const stale = failure.errors.some((error) => error.code === "staleRowVersion");
      if (stale && typeof failure.data === "object" && failure.data !== null) {
        const current = failure.data as Record<string, unknown>;
        const resolution = resolveStaleSave(changes, loadedRecord, current, attempt);
        if (resolution.kind === "alreadyCurrent") {
          // The other save already carries every typed value: rebase, done.
          rebase(current);
          setSavedAt(new Date().toLocaleTimeString());
          return;
        }
        if (resolution.kind === "retry") {
          // Invisible auto-retry: the other save touched different fields.
          setLoadedRecord(current);
          await patch(resolution.changes, resolution.rowVersion, attempt + 1);
          return;
        }
        setLoadedRecord(current);
        setMerge(resolution);
        return;
      }
      placeServerErrors(failure.errors);
    }
  };

  const requestSave = async (): Promise<void> => {
    if (saving) {
      return;
    }
    // The save sweep (REQ-033): every editable field, all problems at once,
    // focus the first in display order.
    const problems: Record<string, string> = {};
    for (const field of editableFields) {
      const problem = validateOnExit(field, values[field.fieldName]);
      if (problem !== null) {
        problems[field.fieldName] = problem;
      }
    }
    if (Object.keys(problems).length > 0) {
      setFieldErrors(problems);
      const first = editableFields.find((field) => field.fieldName in problems);
      if (first !== undefined) {
        focusField(first.fieldName);
      }
      return;
    }
    if (!isDirty) {
      // NothingToSave: a declared no-op — no PATCH travels.
      return;
    }
    setSaving(true);
    try {
      await patch(dirty, rowVersion, 1);
    } finally {
      setSaving(false);
    }
  };

  const cancel = (): void => {
    // Cancel reverts to the originals and STAYS OPEN (revertToOriginal).
    setValues(original);
    setFieldErrors({});
    setFormErrors([]);
    setMerge(null);
  };

  const requestLeave = (): void => {
    if (!isDirty) {
      onLeave();
      return;
    }
    const labels = editableFields
      .filter((field) => field.fieldName in dirty)
      .map((field) => field.fieldLabel);
    setGuard(leaveWarning(labels));
  };

  const formKeyDown = (event: KeyboardEvent<HTMLFormElement>): void => {
    if (event.key === "s" && event.ctrlKey) {
      // Ctrl+S = Save everywhere a Save exists (REQ-038).
      event.preventDefault();
      void requestSave();
      return;
    }
    if (event.key === "Escape") {
      // Escape requests leave — the SAME dirty guard as any navigation.
      event.preventDefault();
      requestLeave();
      return;
    }
    if (event.key === "Enter") {
      // Enter never submits a multi-field form: it acts on the focused
      // control only (checkbox toggles, select opens, rich text newlines).
      const target = event.target as HTMLElement;
      const activates =
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLButtonElement ||
        target instanceof HTMLSelectElement ||
        target.isContentEditable;
      if (!activates) {
        event.preventDefault();
      }
      return;
    }
    if (event.key === "Tab") {
      // The restricted wrapping cycle (form_keyboard.next_tab_stop): Tab
      // stops ONLY on editable fields — never labels, read-only elements,
      // Save, or Cancel.
      const names = editableFields.map((field) => field.fieldName);
      if (names.length === 0) {
        return;
      }
      event.preventDefault();
      const active = document.activeElement?.id ?? "";
      const focused = active.startsWith("field-")
        ? active.slice("field-".length)
        : null;
      const index = focused === null ? -1 : names.indexOf(focused);
      const step = event.shiftKey ? -1 : 1;
      // Re-entry from outside the cycle (Save, a label, a read-only element)
      // lands on the first stop — last when Shift+Tab (next_tab_stop's rule).
      const nextIndex =
        index === -1
          ? event.shiftKey
            ? names.length - 1
            : 0
          : (index + step + names.length) % names.length;
      const next = names[nextIndex];
      if (next !== undefined) {
        focusField(next);
      }
    }
  };

  return (
    <form
      ref={containerRef}
      className="edit-form"
      aria-label={`Edit ${entityType}`}
      onSubmit={(event) => {
        event.preventDefault();
      }}
      onKeyDown={formKeyDown}
    >
      {formErrors.length > 0 && (
        <div role="alert" className="form-level-errors">
          <ul>
            {formErrors.map((error) => (
              <li key={`${error.code}:${error.fieldName ?? ""}`}>
                {error.fieldName !== null ? `${error.fieldName}: ` : ""}
                {error.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {merge !== null && (
        <div
          role="alertdialog"
          className="merge-walkthrough"
          aria-label="Resolve conflicting changes"
        >
          <EducateNotice
            notice={{
              whatHappened: "Someone else saved this record while you were editing.",
              why:
                "These fields were changed by both of you: " +
                merge.conflicts.map((conflict) => conflict.fieldName).join(", ") +
                ".",
              whatNext: "Choose whose value to keep for each field, then save again.",
            }}
          />
          <table className="merge-table">
            <thead>
              <tr>
                <th>Field</th>
                <th>Your value</th>
                <th>Their value</th>
              </tr>
            </thead>
            <tbody>
              {merge.conflicts.map((conflict) => (
                <tr key={conflict.fieldName}>
                  <td>
                    {fieldsByName.get(conflict.fieldName)?.fieldLabel ??
                      conflict.fieldName}
                  </td>
                  <td>
                    <button
                      type="button"
                      onClick={() => {
                        setValue(conflict.fieldName, conflict.yourValue);
                      }}
                    >
                      Keep mine: {formatFieldValue(conflict.yourValue)}
                    </button>
                  </td>
                  <td>
                    <button
                      type="button"
                      onClick={() => {
                        setValue(conflict.fieldName, conflict.theirValue);
                      }}
                    >
                      Take theirs: {formatFieldValue(conflict.theirValue)}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button
            type="button"
            onClick={() => {
              // The walk-through resolved: the next save commits against the
              // fresh version, carrying the user's per-field choices.
              setRowVersion(merge.rowVersion);
              setMerge(null);
            }}
          >
            Done — review and save again
          </button>
        </div>
      )}
      <div className="edit-form-fields">
        {payload.fields.map((field) =>
          field.editable ? (
            <div key={field.fieldName} className="form-field">
              <FieldLabel field={field} htmlFor={`field-${field.fieldName}`} />
              <FieldControl
                entityType={entityType}
                field={field}
                value={values[field.fieldName]}
                invalid={field.fieldName in fieldErrors}
                onChange={(value) => {
                  setValue(field.fieldName, value);
                }}
                onExit={() => {
                  void exitField(field);
                }}
                onSmartPaste={(text) => {
                  void smartPaste(field, text);
                }}
              />
              {field.fieldName in fieldErrors && (
                // Inline at the offending field (MESSAGE_PLACEMENT).
                <p className="field-error" role="alert">
                  {fieldErrors[field.fieldName]}
                </p>
              )}
            </div>
          ) : (
            <div key={field.fieldName} className="form-field form-field-readonly">
              <FieldLabel field={field} />
              {/* REQ-039: usual position, the read value, click explains,
                  no Tab stop — never a grayed-out editor. */}
              <button
                type="button"
                tabIndex={-1}
                className="readonly-value"
                data-readonly-kind={field.readOnly?.kind}
                onClick={() => {
                  if (field.readOnly !== null) {
                    setExplain(field.readOnly.explanation);
                  }
                }}
              >
                {formatFieldValue(payload.record[field.fieldName])}
              </button>
            </div>
          ),
        )}
      </div>
      {explain !== null && (
        <div
          role="dialog"
          aria-label="Why this field is not editable"
          className="field-explain"
        >
          <EducateNotice notice={explain} />
          <button
            type="button"
            tabIndex={-1}
            onClick={() => {
              setExplain(null);
            }}
          >
            Got it
          </button>
        </div>
      )}
      {guard !== null && (
        <div role="alertdialog" aria-label="Unsaved changes" className="leave-guard">
          <EducateNotice notice={guard} />
          <div className="leave-guard-actions">
            <button
              type="button"
              onClick={() => {
                setGuard(null);
                void requestSave();
              }}
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => {
                // The explicit discard: edits abandoned, navigation proceeds.
                setGuard(null);
                setValues(original);
                onLeave();
              }}
            >
              Discard changes
            </button>
            <button
              type="button"
              onClick={() => {
                setGuard(null);
              }}
            >
              Keep editing
            </button>
          </div>
        </div>
      )}
      <footer className="edit-form-footer">
        {savedAt !== null && (
          <span className="save-confirmation">Saved at {savedAt}</span>
        )}
        {/* The large Save (REQ-032) — prominence declared by the server frame.
            Not a Tab stop (REQ-038): Ctrl+S is the keyboard's path to Save. */}
        <button
          type="button"
          tabIndex={-1}
          className="save-button save-button-large"
          data-prominence={payload.screen.save.prominence}
          title={payload.screen.save.shortcut}
          onClick={() => {
            void requestSave();
          }}
        >
          {payload.screen.save.label}
        </button>
        <button
          type="button"
          tabIndex={-1}
          className="cancel-button"
          data-behavior={payload.screen.cancel.behavior}
          onClick={cancel}
        >
          {payload.screen.cancel.label}
        </button>
        <button
          type="button"
          tabIndex={-1}
          className="leave-button"
          onClick={requestLeave}
        >
          Close
        </button>
      </footer>
    </form>
  );
}
