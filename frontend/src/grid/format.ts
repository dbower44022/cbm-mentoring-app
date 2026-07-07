/**
 * The ONE cell-value formatter (FND-909 D1). SKL-112 makes a column's
 * format a view property: the server's column payload declares a format
 * kind, and every grid cell renders through this module keyed by it —
 * no component ever improvises a date string from a raw SQL value. The
 * backend's vocabulary home is `mentorapp/storage/columns.py`; this file
 * is its one frontend mirror (one canonical module per side, never a
 * third copy). The renderings follow the stakeholder-approved prototype's
 * fmtDate flavor: dates "Jun 23, 2026", datetimes "Jul 10, 10:00 AM",
 * absent values an em dash.
 */

/** The closed set of format kinds a served column may declare. */
export type ColumnFormat = "text" | "date" | "datetime" | "number";

/** The prototype's empty-cell marker: absence reads as a dash, never blank. */
export const EMPTY_CELL = "—";

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

// The wire carries the raw driver string ("2026-06-23", "2026-07-10
// 10:00:00.000000", ISO "T" separators, optional zone suffix). Only the
// leading date and hh:mm matter for rendering; seconds/microseconds/zone
// are deliberately ignored — the values are the CRM's stated wall-clock
// times, rendered verbatim like the prototype, never shifted through the
// viewer's timezone.
const DATE_TIME_PATTERN = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/;

function monthDay(month: number, day: number): string {
  // Guard the lookup: a 13th month means the value wasn't a date at all.
  const name = MONTHS[month - 1];
  return name === undefined ? "" : `${name} ${String(day)}`;
}

/** "Jun 23, 2026" — the prototype's date-only rendering (year included). */
function formatDate(raw: string): string {
  const match = DATE_TIME_PATTERN.exec(raw);
  // The date groups always capture on a match; TS can't see that, so the
  // undefined arms simply share the not-a-date fallback.
  const [, year, month, day] = match ?? [];
  if (year === undefined || month === undefined || day === undefined) {
    return raw; // Not a date shape: the raw value is more honest than "".
  }
  const prefix = monthDay(Number(month), Number(day));
  return prefix === "" ? raw : `${prefix}, ${year}`;
}

/** "Jul 10, 10:00 AM" — day plus 12-hour wall-clock; no year, no zone math. */
function formatDatetime(raw: string): string {
  const match = DATE_TIME_PATTERN.exec(raw);
  const [, year, month, day, hours, minutes] = match ?? [];
  if (year === undefined || month === undefined || day === undefined) {
    return raw;
  }
  const prefix = monthDay(Number(month), Number(day));
  if (prefix === "") {
    return raw;
  }
  if (hours === undefined || minutes === undefined) {
    // A datetime column can serve a bare date (e.g. a DATE-typed rollup);
    // rendering the date beats inventing a midnight that was never stated.
    return `${prefix}, ${year}`;
  }
  const hour = Number(hours);
  const half = hour >= 12 ? "PM" : "AM";
  const clockHour = hour % 12 === 0 ? 12 : hour % 12;
  return `${prefix}, ${String(clockHour)}:${minutes} ${half}`;
}

/**
 * Render one cell value by its column's declared format kind.
 *
 * Null, undefined, and empty values become the dash for EVERY kind — one
 * absence marker across the grid. Numbers (counts included) render as
 * plain numerals; text renders verbatim; a value that doesn't parse as its
 * declared kind falls back to its raw string rather than pretending.
 */
export function formatCell(
  value: string | number | null | undefined,
  format: ColumnFormat,
): string {
  if (value === null || value === undefined || value === "") {
    return EMPTY_CELL;
  }
  const raw = String(value);
  switch (format) {
    case "date":
      return formatDate(raw);
    case "datetime":
      return formatDatetime(raw);
    case "number":
    case "text":
      return raw;
  }
}
