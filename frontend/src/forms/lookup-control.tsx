/**
 * The relationship lookup control (REQ-036, REL-004 block 1): the ONE
 * control every registry `"reference"` field renders — `ui/lookup_control.py`
 * (LOOKUP_CONTROL, the suggestion phases) is the behavior contract; the
 * suggestion read is `GET /lookups/{hostEntity}/{fieldName}?q=`.
 *
 * - The dropdown renders the served phase verbatim: keep-typing below the
 *   app's one liveness threshold, matches with the server's full-set count,
 *   no-matches, and no-access as a rendered educate message — the field
 *   stays visible and explains, never hides.
 * - The value written is the record ID only (the FK); the title is
 *   display-only and may go stale — the preview's stance.
 * - Two inline affordances, always visible: Open hands the linked record to
 *   the pop-out machinery (nothing linked → explain, never disable); New…
 *   opens the related entity's standard create form in a pop-out. Adoption
 *   of the created record into the field rides the REQ-013 same-user sync
 *   when that lands (block 4); until then the created record is a pop-out
 *   the user links by search.
 */

import { type ReactElement, useState } from "react";

import { callApi } from "../api/envelope";
import { type EducatePayload, type FormFieldPayload } from "../api/payloads";
import { EducateNotice } from "../shell/educate";
import { popOutRecord, popOutRecordCreate } from "../windows/record";

/** ui/lookup_control.related_entity_type: mentorID → mentor (DB-R2/R2b). */
export function relatedEntityType(fieldName: string): string {
  if (fieldName.length <= 2 || !fieldName.endsWith("ID")) {
    throw new Error(`not an entity-named reference field: ${fieldName}`);
  }
  return fieldName.slice(0, -2);
}

interface SuggestionRef {
  entityType: string;
  recordId: string;
  title: string;
}

interface LookupSuggestionsPayload {
  phase: string;
  suggestions: SuggestionRef[];
  totalMatches: number;
  summary: string | null;
  message: EducatePayload | null;
}

export interface LookupControlProps {
  /** The HOST entity — the lookup read is keyed off its field's settings. */
  entityType: string;
  field: FormFieldPayload;
  /** The FK value (record ID) or null — the only thing the form writes. */
  value: unknown;
  invalid: boolean;
  onChange: (recordId: string | null) => void;
  onExit: () => void;
}

export function LookupControl({
  entityType,
  field,
  value,
  invalid,
  onChange,
  onExit,
}: LookupControlProps): ReactElement {
  const related = relatedEntityType(field.fieldName);
  const [searchText, setSearchText] = useState("");
  const [outcome, setOutcome] = useState<LookupSuggestionsPayload | null>(null);
  const [selectedTitle, setSelectedTitle] = useState<string | null>(null);
  const [explain, setExplain] = useState<EducatePayload | null>(null);
  const [open, setOpen] = useState(false);

  const linkedId = typeof value === "string" && value !== "" ? value : null;

  const query = async (text: string): Promise<void> => {
    try {
      const result = await callApi<LookupSuggestionsPayload>(
        `/lookups/${entityType}/${field.fieldName}?q=${encodeURIComponent(text)}`,
      );
      setOutcome(result.data);
    } catch {
      // The dropdown simply stays as it was; the field itself never blocks.
    }
  };

  const displayText = open ? searchText : (selectedTitle ?? linkedId ?? "");

  return (
    <div className="lookup-control">
      <div className="lookup-entry-row">
        <input
          id={`field-${field.fieldName}`}
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-invalid={invalid || undefined}
          aria-label={field.fieldLabel}
          value={displayText}
          onFocus={() => {
            setOpen(true);
            setSearchText("");
          }}
          onChange={(event) => {
            const text = event.target.value;
            setOpen(true);
            setSearchText(text);
            if (text === "") {
              // Clearing the text clears the link — an empty lookup is null.
              setSelectedTitle(null);
              onChange(null);
            }
            void query(text);
          }}
          onBlur={() => {
            setOpen(false);
            onExit();
          }}
        />
        {/* The two inline affordances — always visible, never disabled. */}
        <button
          type="button"
          tabIndex={-1}
          className="lookup-open"
          onClick={() => {
            if (linkedId === null) {
              setExplain({
                whatHappened: "'Open' didn't run.",
                why: `No ${field.fieldLabel} is linked yet.`,
                whatNext: "Pick a record first, then Open shows it.",
              });
              return;
            }
            popOutRecord(related, linkedId);
          }}
        >
          Open
        </button>
        <button
          type="button"
          tabIndex={-1}
          className="lookup-create"
          onClick={() => {
            popOutRecordCreate(related);
          }}
        >
          New…
        </button>
      </div>
      {open && outcome !== null && (
        <div className="lookup-dropdown" data-phase={outcome.phase}>
          {outcome.message !== null && <EducateNotice notice={outcome.message} />}
          {outcome.summary !== null && (
            <p className="lookup-summary">{outcome.summary}</p>
          )}
          {outcome.phase === "matches" && (
            <ul className="lookup-suggestions" role="listbox">
              {outcome.suggestions.map((suggestion) => (
                <li key={suggestion.recordId}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={suggestion.recordId === linkedId}
                    // Fires before the input's blur closes the dropdown.
                    onMouseDown={(event) => {
                      event.preventDefault();
                      setSelectedTitle(suggestion.title);
                      setOpen(false);
                      onChange(suggestion.recordId);
                    }}
                  >
                    {suggestion.title}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {explain !== null && (
        <div role="dialog" aria-label="Why this didn't run" className="field-explain">
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
    </div>
  );
}
