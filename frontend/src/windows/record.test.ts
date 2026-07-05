/**
 * Pure-logic tests for the record-window helpers (no DOM, matching the
 * envelope suite's style): read-optimized value formatting and the
 * one-window-per-record naming that backs the switch-to-that-window rule.
 */

import { describe, expect, it } from "vitest";

import { formatFieldValue, popOutWindowName } from "./record";

describe("formatFieldValue", () => {
  it("renders absence as a dash, never 'null'", () => {
    expect(formatFieldValue(null)).toBe("—");
    expect(formatFieldValue(undefined)).toBe("—");
  });

  it("renders booleans as words", () => {
    expect(formatFieldValue(true)).toBe("Yes");
    expect(formatFieldValue(false)).toBe("No");
  });

  it("passes strings and numbers through", () => {
    expect(formatFieldValue("Ada")).toBe("Ada");
    expect(formatFieldValue(0)).toBe("0");
  });

  it("falls back to JSON for structured values", () => {
    expect(formatFieldValue({ a: 1 })).toBe('{"a":1}');
  });
});

describe("popOutWindowName", () => {
  it("is the record identity, so one record maps to one window", () => {
    expect(popOutWindowName("mentor", "abc")).toBe("record:mentor:abc");
    expect(popOutWindowName("mentor", "abc")).toBe(popOutWindowName("mentor", "abc"));
    expect(popOutWindowName("mentor", "abc")).not.toBe(popOutWindowName("mentee", "abc"));
  });
});
