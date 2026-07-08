/**
 * The create form (REQ-037, REL-004 block 1): THE SAME full-screen form
 * editing uses (`api/record_create.py CREATE_FORM` declares it), opened
 * empty and pre-filled from field-setting defaults, with the non-blocking
 * similar-records offer in front of Save:
 *
 * - Identity fields (the duplicate-match rules' members) arm the advisory
 *   check as they land; the offer compares side by side and presents
 *   continue-or-switch — NEVER a wall in front of Save. A removed match
 *   offers restore instead of create.
 * - Save POSTs the whole record; the server's own duplicate rejection (409)
 *   re-presents the same offer ENFORCED — continuing resubmits with the
 *   recorded override (REQ-059: allowed, and remembered).
 * - After the first save the user lands on the new record's read view;
 *   Cancel beforehand creates nothing. The dirty baseline is the seed, so a
 *   form still at its defaults closes without ceremony.
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
  type CreateFormPayload,
  type EducatePayload,
  type SimilarCandidatePayload,
  type SimilarRecordsPayload,
} from "../api/payloads";
import { EducateNotice } from "../shell/educate";
import { formatFieldValue, popOutRecord } from "../windows/record";
import {
  blankToNull,
  dirtyChanges,
  FieldControl,
  FieldLabel,
  leaveWarning,
  validateOnExit,
} from "./edit-form";

/** The candidate comparison + choices (REQ-037): compare, then choose. */
function SimilarRecordsOffer({
  entityType,
  candidates,
  enforced,
  yourValues,
  onContinue,
  onSwitch,
  onRestore,
}: {
  entityType: string;
  candidates: SimilarCandidatePayload[];
  /** False = advisory (continue dismisses); true = the server's rejection
   * (continue resubmits with the recorded override). */
  enforced: boolean;
  yourValues: Record<string, unknown>;
  onContinue: (overrideReason: string) => void;
  onSwitch: (recordId: string) => void;
  onRestore: (candidate: SimilarCandidatePayload) => void;
}): ReactElement {
  const [reason, setReason] = useState("");
  const idField = `${entityType}ID`;
  const shownFields = Object.keys(yourValues).filter(
    (name) => blankToNull(yourValues[name]) !== null,
  );
  return (
    <div
      role="dialog"
      aria-label="Similar records exist"
      className="similar-records-offer"
      data-blocking="false"
    >
      <p className="offer-headline">
        {String(candidates.length)} similar record
        {candidates.length === 1 ? "" : "s"} exist{candidates.length === 1 ? "s" : ""}.
        Compare before saving — you can always continue.
      </p>
      {/* Side-by-side comparison (COMPARISON_PRESENTATION): the new values
          beside each candidate, so "is this the same one?" is answered by
          looking, not guessing. */}
      <table className="offer-comparison">
        <thead>
          <tr>
            <th>Field</th>
            <th>Your new record</th>
            {candidates.map((candidate, index) => (
              <th key={String(candidate.record[idField])}>
                Existing {String(index + 1)}
                {candidate.removed ? " (removed)" : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shownFields.map((name) => (
            <tr key={name}>
              <td>{name}</td>
              <td>{formatFieldValue(yourValues[name])}</td>
              {candidates.map((candidate) => (
                <td key={String(candidate.record[idField])}>
                  {formatFieldValue(candidate.record[name])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="offer-actions">
        {enforced && (
          <label className="offer-reason">
            Why is this not a duplicate? (optional)
            <input
              type="text"
              value={reason}
              onChange={(event) => {
                setReason(event.target.value);
              }}
            />
          </label>
        )}
        <button
          type="button"
          onClick={() => {
            onContinue(reason);
          }}
        >
          {enforced ? "Continue and create anyway" : "Continue"}
        </button>
        {candidates.map((candidate, index) => (
          <span
            key={String(candidate.record[idField])}
            className="offer-candidate-actions"
          >
            <button
              type="button"
              onClick={() => {
                popOutRecord(entityType, String(candidate.record[idField]));
              }}
            >
              Open existing {String(index + 1)}
            </button>
            {candidate.removed ? (
              <button
                type="button"
                onClick={() => {
                  onRestore(candidate);
                }}
              >
                Restore existing {String(index + 1)} instead
              </button>
            ) : (
              <button
                type="button"
                onClick={() => {
                  onSwitch(String(candidate.record[idField]));
                }}
              >
                Use existing {String(index + 1)} instead
              </button>
            )}
          </span>
        ))}
      </div>
    </div>
  );
}

export interface CreateFormScreenProps {
  entityType: string;
  payload: CreateFormPayload;
  /** The first save landed (create or restore): go to the read view. */
  onCreated: (recordId: string) => void;
  /** Cancel/leave before the first save: nothing was created. */
  onLeave: () => void;
}

export function CreateFormScreen({
  entityType,
  payload,
  onCreated,
  onLeave,
}: CreateFormScreenProps): ReactElement {
  const editableFields = useMemo(
    () => payload.fields.filter((field) => field.editable),
    [payload.fields],
  );
  const fieldsByName = useMemo(
    () => new Map(payload.fields.map((field) => [field.fieldName, field])),
    [payload.fields],
  );
  const identityFields = useMemo(
    () => new Set(payload.identityFieldNames),
    [payload.identityFieldNames],
  );

  const seed = useMemo<Record<string, unknown>>(
    () =>
      Object.fromEntries(
        editableFields.map((field) => [
          field.fieldName,
          payload.seed[field.fieldName] ?? null,
        ]),
      ),
    [editableFields, payload.seed],
  );
  const [values, setValues] = useState<Record<string, unknown>>(seed);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formErrors, setFormErrors] = useState<ApiError[]>([]);
  const [offer, setOffer] = useState<{
    candidates: SimilarCandidatePayload[];
    enforced: boolean;
  } | null>(null);
  const [guard, setGuard] = useState<EducatePayload | null>(null);
  const [saving, setSaving] = useState(false);
  // The advisory check runs once per identity-values shape; remembering the
  // last checked shape keeps one blur from re-offering what was dismissed.
  const [lastChecked, setLastChecked] = useState<string | null>(null);

  const containerRef = useRef<HTMLFormElement>(null);

  // Authored input = differences from the seed (the dirty baseline).
  const authored = dirtyChanges(seed, values);
  const isDirty = Object.keys(authored).length > 0;

  useEffect(() => {
    if (payload.initialFocusField !== null) {
      focusField(payload.initialFocusField);
    }
    // The payload is this window's one load — first render only.
  }, []);

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
    const control = containerRef.current?.querySelector<HTMLElement>(
      `[id="field-${fieldName}"]`,
    );
    control?.focus();
  };

  const nonNullValues = (): Record<string, unknown> =>
    Object.fromEntries(
      Object.entries(values)
        .map(([name, value]) => [name, blankToNull(value)] as const)
        .filter(([, value]) => value !== null && value !== undefined),
    );

  const identityValues = (): Record<string, unknown> =>
    Object.fromEntries(
      Object.entries(nonNullValues()).filter(([name]) => identityFields.has(name)),
    );

  /** The advisory pre-save check (REQ-037): armed when identity fields land. */
  const checkSimilar = async (): Promise<void> => {
    const armed = identityValues();
    const shape = JSON.stringify(armed);
    if (Object.keys(armed).length === 0 || shape === lastChecked) {
      return;
    }
    setLastChecked(shape);
    try {
      const result = await callApi<SimilarRecordsPayload>(
        `/records/${entityType}/similar-records`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ values: armed }),
        },
      );
      if (result.data.candidates.length > 0) {
        setOffer({ candidates: result.data.candidates, enforced: false });
      }
    } catch {
      // Advisory means advisory: a failed check never blocks entry; the
      // server's save-time detection still stands behind it.
    }
  };

  const exitField = (fieldName: string): void => {
    const field = fieldsByName.get(fieldName);
    if (field === undefined) {
      return;
    }
    const problem = validateOnExit(field, values[fieldName]);
    setFieldErrors((current) => {
      const next = Object.fromEntries(
        Object.entries(current).filter(([name]) => name !== fieldName),
      );
      if (problem !== null) {
        next[fieldName] = problem;
      }
      return next;
    });
    if (identityFields.has(fieldName)) {
      void checkSimilar();
    }
  };

  const placeServerErrors = (errors: ApiError[]): void => {
    const inline: Record<string, string> = {};
    const formLevel: ApiError[] = [];
    for (const error of errors) {
      if (error.fieldName !== null && fieldsByName.has(error.fieldName)) {
        inline[error.fieldName] = error.message;
      } else {
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

  const post = async (
    overrideDuplicates: boolean,
    overrideReason: string,
  ): Promise<void> => {
    setSaving(true);
    try {
      const result = await callApi<{ record: Record<string, unknown> }>(
        `/records/${entityType}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            values: nonNullValues(),
            ...(overrideDuplicates
              ? {
                  overrideDuplicates: true,
                  ...(overrideReason.trim() !== ""
                    ? { overrideReason: overrideReason }
                    : {}),
                }
              : {}),
          }),
        },
      );
      // The first save lands on the new record's read view (CREATE_LANDING).
      onCreated(String(result.data.record[`${entityType}ID`]));
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
      const duplicate = failure.errors.some(
        (error) => error.code === "duplicateCandidates",
      );
      if (duplicate && Array.isArray(failure.data)) {
        // The server's DB-S12 rejection: the SAME offer, enforced —
        // continuing resubmits with the recorded override (REQ-059).
        setOffer({
          candidates: (failure.data as Record<string, unknown>[]).map((record) => ({
            record,
            removed: false,
          })),
          enforced: true,
        });
        return;
      }
      placeServerErrors(failure.errors);
    } finally {
      setSaving(false);
    }
  };

  const requestSave = async (): Promise<void> => {
    if (saving) {
      return;
    }
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
    await post(false, "");
  };

  const restoreInstead = async (candidate: SimilarCandidatePayload): Promise<void> => {
    const recordId = String(candidate.record[`${entityType}ID`]);
    try {
      await callApi(`/records/${entityType}/${recordId}/restore`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rowVersion: Number(candidate.record.rowVersion) }),
      });
      // Same landing as a create: the record now exists and I'm looking at it.
      onCreated(recordId);
    } catch (failure: unknown) {
      setFormErrors(
        failure instanceof EnvelopeError
          ? failure.errors
          : [
              {
                fieldName: null,
                code: "unreachable",
                message: "The restore didn't reach the server. Try again.",
              },
            ],
      );
    }
  };

  const requestLeave = (): void => {
    if (!isDirty) {
      onLeave();
      return;
    }
    const labels = editableFields
      .filter((field) => field.fieldName in authored)
      .map((field) => field.fieldLabel);
    setGuard(leaveWarning(labels));
  };

  const formKeyDown = (event: KeyboardEvent<HTMLFormElement>): void => {
    if (event.key === "s" && event.ctrlKey) {
      event.preventDefault();
      void requestSave();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      requestLeave();
      return;
    }
    if (event.key === "Enter") {
      const target = event.target as HTMLElement;
      const activates =
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLButtonElement ||
        target instanceof HTMLSelectElement ||
        target.isContentEditable;
      if (!activates) {
        event.preventDefault();
      }
    }
  };

  return (
    <form
      ref={containerRef}
      className="edit-form create-form"
      aria-label={`New ${entityType}`}
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
      {offer !== null && (
        <SimilarRecordsOffer
          entityType={entityType}
          candidates={offer.candidates}
          enforced={offer.enforced}
          yourValues={nonNullValues()}
          onContinue={(overrideReason) => {
            const enforced = offer.enforced;
            setOffer(null);
            if (enforced) {
              // Continue past the server's rejection: the override travels
              // and is recorded with the create (REQ-059).
              void post(true, overrideReason);
            }
            // Advisory continue just dismisses — Save remains the user's act.
          }}
          onSwitch={(recordId) => {
            // The candidate IS the record: open it, create nothing.
            setOffer(null);
            onCreated(recordId);
          }}
          onRestore={(candidate) => {
            setOffer(null);
            void restoreInstead(candidate);
          }}
        />
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
                  setValues((current) => ({ ...current, [field.fieldName]: value }));
                }}
                onExit={() => {
                  exitField(field.fieldName);
                }}
              />
              {field.fieldName in fieldErrors && (
                <p className="field-error" role="alert">
                  {fieldErrors[field.fieldName]}
                </p>
              )}
            </div>
          ) : (
            // A computed field on a create form: in place, read-rendered,
            // empty — it fills itself once the record exists (REQ-039).
            <div key={field.fieldName} className="form-field form-field-readonly">
              <FieldLabel field={field} />
              <span className="readonly-value">—</span>
            </div>
          ),
        )}
      </div>
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
                // Cancel creates nothing and returns to the origin.
                setGuard(null);
                onLeave();
              }}
            >
              Discard and leave
            </button>
            <button
              type="button"
              onClick={() => {
                setGuard(null);
              }}
            >
              Keep entering
            </button>
          </div>
        </div>
      )}
      <footer className="edit-form-footer">
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
          data-behavior={payload.form.cancelCreates}
          onClick={requestLeave}
        >
          {payload.screen.cancel.label}
        </button>
      </footer>
    </form>
  );
}
