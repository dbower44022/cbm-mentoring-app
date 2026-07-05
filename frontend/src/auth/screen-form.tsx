/**
 * One credential screen, rendered verbatim from its served AuthScreen payload
 * (WTK-005): field order, masking, links, and Enter-submits come from the
 * declaration, never re-decided here. Focus starts on the first editable
 * field; read-only fields display but never take focus (forms standard —
 * tab stops only on editable fields).
 */

import { type FormEvent, type ReactElement, useEffect, useRef, useState } from "react";
import type { AuthScreenPayload, EducateMessagePayload } from "./payloads";

export interface ScreenFormProps {
  screen: AuthScreenPayload;
  /** Values for read-only fields (the re-auth screen's pinned username). */
  fixedValues?: Record<string, string>;
  busy: boolean;
  message: EducateMessagePayload | null;
  onSubmit: (values: Record<string, string>) => void;
  /** A link was activated; identified by its index in screen.links. */
  onLink: (index: number) => void;
}

export function EducateNotice({
  message,
}: {
  message: EducateMessagePayload;
}): ReactElement {
  return (
    <div className="educate-message" role="alert">
      <p className="educate-what">{message.whatHappened}</p>
      <p className="educate-why">{message.why}</p>
      <p className="educate-next">{message.whatNext}</p>
    </div>
  );
}

export function ScreenForm({
  screen,
  fixedValues = {},
  busy,
  message,
  onSubmit,
  onLink,
}: ScreenFormProps): ReactElement {
  const [values, setValues] = useState<Record<string, string>>(fixedValues);
  const firstEditableRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    firstEditableRef.current?.focus();
  }, [screen.key]);

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    if (!busy) {
      onSubmit(values);
    }
  };

  const firstEditable = screen.fields.find((field) => !field.readOnly);

  return (
    <form
      className="auth-screen"
      onSubmit={submit}
      aria-busy={busy}
      onKeyDown={(event) => {
        // enterSubmits is declared per screen (true on every credential
        // screen — the marked deviation auth_flows records); render it, don't
        // re-decide it.
        if (event.key === "Enter" && !screen.enterSubmits) {
          event.preventDefault();
        }
      }}
    >
      <h1>{screen.title}</h1>
      {message !== null && <EducateNotice message={message} />}
      {screen.fields.map((field) => (
        <label key={field.name} className="auth-field">
          <span>{field.label}</span>
          <input
            ref={field === firstEditable ? firstEditableRef : undefined}
            type={field.control}
            name={field.name}
            value={values[field.name] ?? ""}
            readOnly={field.readOnly}
            tabIndex={field.readOnly ? -1 : undefined}
            onChange={(event) => {
              if (!field.readOnly) {
                setValues((prior) => ({ ...prior, [field.name]: event.target.value }));
              }
            }}
          />
        </label>
      ))}
      {/* Never disabled (educate-never-hide, app-wide): a submit while one is
          already in flight is simply not re-sent — the busy state explains. */}
      <button type="submit" className="auth-submit">
        {busy ? "Working…" : screen.submitLabel}
      </button>
      {screen.links.map((label, index) => (
        <button
          key={label}
          type="button"
          className="auth-link"
          onClick={() => {
            onLink(index);
          }}
        >
          {label}
        </button>
      ))}
    </form>
  );
}
