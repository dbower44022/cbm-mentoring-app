/**
 * The Ctrl+K quick-open palette (DEC-080 §E): opening immediately queries
 * GET /shell/quick-open with an empty q — the palette opens as the full
 * catalog of every panel and view the user can reach — then re-queries
 * debounced as the user types. Entries render kind + label verbatim;
 * activation routes to the panelKey, passing viewKey when set (null means
 * the panel's last-displayed view). meta.totalCount is the result count.
 */

import { type ReactElement, useEffect, useRef, useState } from "react";
import { callApi } from "../api/envelope";
import type { QuickOpenEntryPayload, QuickOpenPayload } from "./payloads";

const DEBOUNCE_MS = 150;

export interface QuickOpenProps {
  onActivate: (panelKey: string, viewKey: string | null) => void;
  onClose: () => void;
}

export function QuickOpen({ onActivate, onClose }: QuickOpenProps): ReactElement {
  const [query, setQuery] = useState("");
  const [entries, setEntries] = useState<QuickOpenEntryPayload[]>([]);
  const [totalCount, setTotalCount] = useState<number | null>(null);
  const [focusIndex, setFocusIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    let cancelled = false;
    const fetchEntries = (): void => {
      void callApi<QuickOpenPayload>(
        `/shell/quick-open?q=${encodeURIComponent(query)}`,
      ).then(({ data, meta }) => {
        if (!cancelled) {
          setEntries(data.entries);
          setTotalCount(typeof meta.totalCount === "number" ? meta.totalCount : null);
          setFocusIndex(0);
        }
      });
    };
    // The empty query is the open act itself — serve the catalog immediately;
    // only typed refinements debounce.
    if (query === "") {
      fetchEntries();
      return () => {
        cancelled = true;
      };
    }
    const timer = setTimeout(fetchEntries, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

  const activate = (entry: QuickOpenEntryPayload): void => {
    onActivate(entry.panelKey, entry.viewKey);
    onClose();
  };

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="Quick open">
      <div className="quick-open">
        <input
          ref={inputRef}
          type="text"
          placeholder="Go to panel or view…"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              onClose();
            } else if (event.key === "ArrowDown") {
              event.preventDefault();
              setFocusIndex((index) => Math.min(index + 1, entries.length - 1));
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setFocusIndex((index) => Math.max(index - 1, 0));
            } else if (event.key === "Enter") {
              const entry = entries[focusIndex];
              if (entry !== undefined) {
                activate(entry);
              }
            }
          }}
        />
        <ul className="quick-open-entries">
          {entries.map((entry, index) => (
            <li key={`${entry.kind}:${entry.panelKey}:${entry.viewKey ?? ""}`}>
              <button
                type="button"
                className={
                  index === focusIndex ? "quick-open-entry focused" : "quick-open-entry"
                }
                onClick={() => {
                  activate(entry);
                }}
              >
                <span className="quick-open-kind">{entry.kind}</span> {entry.label}
              </button>
            </li>
          ))}
        </ul>
        <p className="quick-open-count">
          {totalCount === null ? "" : `${String(totalCount)} destinations`}
        </p>
      </div>
    </div>
  );
}
