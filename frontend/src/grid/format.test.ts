/**
 * The one cell formatter (FND-909 D1): raw wire values in, the prototype's
 * renderings out — dates "Jun 23, 2026", datetimes "Jul 10, 10:00 AM",
 * absent values the em dash, counts as plain numerals. These tests pin the
 * format-kind vocabulary's behavior so no grid cell can regress to showing
 * a raw SQL value.
 */

import { describe, expect, it } from "vitest";

import { EMPTY_CELL, formatCell } from "./format";

describe("date format", () => {
  it("renders a raw date as month day, year — the prototype's fmtDate flavor", () => {
    expect(formatCell("2026-06-23", "date")).toBe("Jun 23, 2026");
    expect(formatCell("2026-12-05", "date")).toBe("Dec 5, 2026");
  });

  it("falls back to the raw string when the value isn't a date shape", () => {
    expect(formatCell("not-a-date", "date")).toBe("not-a-date");
    expect(formatCell("2026-13-01", "date")).toBe("2026-13-01");
  });
});

describe("datetime format", () => {
  it("renders day plus 12-hour time — never the raw SQL form (D1)", () => {
    // The defect's exact raw shape: str(datetime) with microseconds.
    expect(formatCell("2026-07-10 10:00:00.000000", "datetime")).toBe(
      "Jul 10, 10:00 AM",
    );
    expect(formatCell("2026-06-23 15:00:00.000000", "datetime")).toBe(
      "Jun 23, 3:00 PM",
    );
  });

  it("accepts ISO 'T' separators and ignores zone suffixes (stated wall-clock)", () => {
    expect(formatCell("2026-07-10T10:30:00+00:00", "datetime")).toBe(
      "Jul 10, 10:30 AM",
    );
  });

  it("speaks 12 AM at midnight and 12 PM at noon", () => {
    expect(formatCell("2026-07-10 00:05:00", "datetime")).toBe("Jul 10, 12:05 AM");
    expect(formatCell("2026-07-10 12:00:00", "datetime")).toBe("Jul 10, 12:00 PM");
  });

  it("renders a bare date served into a datetime column as the date", () => {
    expect(formatCell("2026-07-10", "datetime")).toBe("Jul 10, 2026");
  });
});

describe("absent values", () => {
  it("renders the dash for null, undefined, and empty — every format kind", () => {
    expect(formatCell(null, "datetime")).toBe(EMPTY_CELL);
    expect(formatCell(null, "date")).toBe(EMPTY_CELL);
    expect(formatCell(undefined, "number")).toBe(EMPTY_CELL);
    expect(formatCell("", "text")).toBe(EMPTY_CELL);
  });
});

describe("number and text formats", () => {
  it("renders counts as plain numerals", () => {
    expect(formatCell(7, "number")).toBe("7");
    expect(formatCell(0, "number")).toBe("0");
  });

  it("renders text verbatim", () => {
    expect(formatCell("Acme Growth", "text")).toBe("Acme Growth");
  });
});
