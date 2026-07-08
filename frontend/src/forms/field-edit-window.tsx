/**
 * The per-field edit window (REQ-035, REL-004 block 1): double-clicking a
 * field's read-only element, anywhere records render read-optimized, opens
 * this SMALL self-contained editor — `ui/field_edit_window.py`
 * (FIELD_EDIT_FRAME) and `api/field_edit.py` (FieldEditors) are the
 * behavior contract:
 *
 * - Own Save and Cancel; Save commits exactly one field as a single-field
 *   PATCH under `rowVersion` (`single_field_patch` — the same verb,
 *   endpoint, and validation as any other edit). Cancel discards; Esc IS
 *   Cancel here (the one place Esc discards directly). No full-record save.
 * - Save with the value back at base is NothingToSave: the window closes
 *   and no write travels (unchanged values never travel, DB-S12).
 * - A stale save routes through the standard resolver: auto-retry when the
 *   other save didn't touch this field, take-theirs/keep-mine on a true
 *   overlap — never a silent overwrite.
 * - The window hosts the SAME entry control the full form gives the type
 *   (rich text and lookups included) — a small window is never a lesser
 *   editor.
 */

import {
  type KeyboardEvent,
  type ReactElement,
  useEffect,
  useRef,
  useState,
} from "react";

import { callApi, EnvelopeError } from "../api/envelope";
import { type EducatePayload, type FormFieldPayload } from "../api/payloads";
import { EducateNotice } from "../shell/educate";
import { formatFieldValue } from "../windows/record";
import { blankToNull, FieldControl, FieldLabel, validateOnExit } from "./edit-form";

export interface FieldEditWindowProps {
  entityType: string;
  recordId: string;
  field: FormFieldPayload;
  /** The value the double-clicked element rendered from. */
  baseValue: unknown;
  /** The record's version at open — the concurrency base (DB-S4). */
  rowVersion: number;
  /** The single-field PATCH landed: the served record rides along. */
  onSaved: (record: Record<string, unknown>) => void;
  /** Cancel/close: discard, no ceremony (Cancel IS the explicit discard). */
  onClose: () => void;
}

export function FieldEditWindow({
  entityType,
  recordId,
  field,
  baseValue,
  rowVersion,
  onSaved,
  onClose,
}: FieldEditWindowProps): ReactElement {
  const [value, setValue] = useState(baseValue);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{
    theirValue: unknown;
    rowVersion: number;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const hostRef = useRef<HTMLDivElement>(null);

  // Focus starts in the editor control — the window's only editable field.
  useEffect(() => {
    hostRef.current
      ?.querySelector<HTMLElement>(`[id="field-${field.fieldName}"]`)
      ?.focus();
  }, [field.fieldName]);

  const patch = async (sendValue: unknown, version: number): Promise<void> => {
    setSaving(true);
    try {
      const result = await callApi<{ record: Record<string, unknown> }>(
        `/records/${entityType}/${recordId}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          // single_field_patch: exactly one field plus rowVersion — an
          // unrelated stale value can never fail this save.
          body: JSON.stringify({ [field.fieldName]: sendValue, rowVersion: version }),
        },
      );
      onSaved(result.data.record);
    } catch (failure: unknown) {
      if (
        failure instanceof EnvelopeError &&
        failure.errors.some((entry) => entry.code === "staleRowVersion") &&
        typeof failure.data === "object" &&
        failure.data !== null
      ) {
        const current = failure.data as Record<string, unknown>;
        const theirValue = current[field.fieldName];
        const freshVersion = Number(current.rowVersion);
        if (theirValue === sendValue) {
          // The other save already carries this value: nothing left to send.
          onSaved(current);
          return;
        }
        if (theirValue === baseValue) {
          // The other save touched OTHER fields: invisible auto-retry
          // against the fresh version (the standard resolver's retry case).
          await patch(sendValue, freshVersion);
          return;
        }
        setConflict({ theirValue, rowVersion: freshVersion });
        return;
      }
      if (failure instanceof EnvelopeError) {
        const [first] = failure.errors;
        setError(first !== undefined ? first.message : "The save was refused.");
        return;
      }
      setError(
        "The save didn't reach the server. Check your connection and try again.",
      );
    } finally {
      setSaving(false);
    }
  };

  const requestSave = async (): Promise<void> => {
    if (saving) {
      return;
    }
    const normalized = blankToNull(value);
    if (normalized === blankToNull(baseValue)) {
      // NothingToSave: the window closes, no write travels (DB-S12).
      onClose();
      return;
    }
    const problem = validateOnExit(field, value);
    if (problem !== null) {
      setError(problem);
      return;
    }
    await patch(normalized, rowVersion);
  };

  const keyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key === "s" && event.ctrlKey) {
      event.preventDefault();
      void requestSave();
      return;
    }
    if (event.key === "Escape") {
      // Esc IS Cancel in this window (FIELD_EDIT_FRAME.escape="cancel"):
      // Cancel is the explicit discard, so Esc takes it directly, no guard.
      event.preventDefault();
      onClose();
    }
  };

  const conflictNotice: EducatePayload | null =
    conflict === null
      ? null
      : {
          whatHappened: `'${field.fieldLabel}' was changed by someone else while you were editing it.`,
          why:
            `Their value is now "${formatFieldValue(conflict.theirValue)}"; ` +
            `yours is "${formatFieldValue(value)}".`,
          whatNext: "Keep your value to overwrite theirs, or take theirs and close.",
        };

  return (
    <div
      ref={hostRef}
      role="dialog"
      aria-label={`Edit ${field.fieldLabel}`}
      className="field-edit-window"
      data-kind="smallWindow"
      data-commits="singleFieldPatch"
      onKeyDown={keyDown}
    >
      <FieldLabel field={field} htmlFor={`field-${field.fieldName}`} />
      <FieldControl
        entityType={entityType}
        field={field}
        value={value}
        invalid={error !== null}
        onChange={(next) => {
          setValue(next);
          setError(null);
        }}
        onExit={() => {
          setError(validateOnExit(field, value));
        }}
      />
      {error !== null && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
      {conflictNotice !== null && conflict !== null && (
        <div role="alertdialog" aria-label="This field changed" className="leave-guard">
          <EducateNotice notice={conflictNotice} />
          <div className="leave-guard-actions">
            <button
              type="button"
              onClick={() => {
                const fresh = conflict.rowVersion;
                setConflict(null);
                void patch(blankToNull(value), fresh);
              }}
            >
              Keep mine
            </button>
            <button
              type="button"
              onClick={() => {
                setConflict(null);
                onClose();
              }}
            >
              Take theirs
            </button>
          </div>
        </div>
      )}
      <footer className="field-edit-actions">
        <button
          type="button"
          className="save-button"
          title="Ctrl+S"
          onClick={() => {
            void requestSave();
          }}
        >
          Save
        </button>
        <button type="button" className="cancel-button" onClick={onClose}>
          Cancel
        </button>
      </footer>
    </div>
  );
}
