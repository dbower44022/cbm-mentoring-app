/**
 * The prep surface's pure state (WTK-188/177): which session prep conducts,
 * and the entry lifecycle — dirty tracking against the saved baseline, the
 * save round trip re-arming rowVersion, refusals that never cost typed work.
 */

import { describe, expect, it } from "vitest";

import { type RollupSessionPayload } from "./payloads";
import { isDirty, loadedEntry, pickPrepSession, reducePrepEntry } from "./prep-model";

function sessionAt(
  id: string,
  iso: string,
  over?: Partial<RollupSessionPayload>,
): RollupSessionPayload {
  return {
    sessionID: id,
    scheduledAt: iso,
    sessionStatus: "scheduled",
    sessionStatusLabel: "Scheduled",
    conferenceLink: null,
    sessionNotes: null,
    actionItems: null,
    rowVersion: 1,
    ...over,
  };
}

const NOW = new Date("2026-07-06T12:00:00Z");

// Newest-first, as the rollup endpoint serves them.
const SESSIONS = [
  sessionAt("s-far", "2026-09-01T15:00:00Z"),
  sessionAt("s-soon", "2026-07-10T15:00:00Z"),
  sessionAt("s-past", "2026-06-01T15:00:00Z"),
];

describe("pickPrepSession", () => {
  it("honors an addressed session id", () => {
    expect(pickPrepSession(SESSIONS, "s-past", NOW)?.sessionID).toBe("s-past");
  });

  it("defaults to the NEXT upcoming session — prep is preparation", () => {
    expect(pickPrepSession(SESSIONS, undefined, NOW)?.sessionID).toBe("s-soon");
  });

  it("falls back to the newest session when nothing is upcoming", () => {
    const held = [
      sessionAt("s-b", "2026-06-02T15:00:00Z"),
      sessionAt("s-a", "2026-06-01T15:00:00Z"),
    ];
    expect(pickPrepSession(held, undefined, NOW)?.sessionID).toBe("s-b");
  });

  it("answers null only when no sessions exist", () => {
    expect(pickPrepSession([], undefined, NOW)).toBeNull();
  });

  it("falls back to the default pick for an unknown addressed id", () => {
    expect(pickPrepSession(SESSIONS, "s-gone", NOW)?.sessionID).toBe("s-soon");
  });
});

describe("the entry lifecycle", () => {
  const loaded = loadedEntry(
    sessionAt("s-1", "2026-07-10T15:00:00Z", {
      sessionNotes: "<p>Existing</p>",
      rowVersion: 3,
    }),
  );

  it("loads clean: the saved content IS the baseline", () => {
    expect(isDirty(loaded)).toBe(false);
    expect(loaded.notes).toBe("<p>Existing</p>");
    expect(loaded.rowVersion).toBe(3);
  });

  it("editing dirties against the baseline, never mutates it", () => {
    const edited = reducePrepEntry(loaded, {
      kind: "edited",
      field: "notes",
      value: "<p>Changed</p>",
    });
    expect(isDirty(edited)).toBe(true);
    expect(edited.savedNotes).toBe("<p>Existing</p>");
  });

  it("a successful save re-baselines and re-arms rowVersion", () => {
    let state = reducePrepEntry(loaded, {
      kind: "edited",
      field: "actionItems",
      value: "<ul><li>Do X</li></ul>",
    });
    state = reducePrepEntry(state, { kind: "saveStarted" });
    expect(state.saving).toBe(true);
    state = reducePrepEntry(state, { kind: "saveSucceeded", rowVersion: 4 });
    expect(isDirty(state)).toBe(false);
    expect(state.rowVersion).toBe(4);
    expect(state.savedActionItems).toBe("<ul><li>Do X</li></ul>");
    expect(state.savedNotice).not.toBeNull();
  });

  it("a refusal keeps the typed entry exactly as it was", () => {
    let state = reducePrepEntry(loaded, {
      kind: "edited",
      field: "notes",
      value: "<p>Typed</p>",
    });
    state = reducePrepEntry(state, { kind: "saveStarted" });
    state = reducePrepEntry(state, {
      kind: "saveRefused",
      errors: [{ fieldName: null, code: "staleRowVersion", message: "changed" }],
    });
    expect(state.notes).toBe("<p>Typed</p>");
    expect(isDirty(state)).toBe(true);
    expect(state.saving).toBe(false);
    expect(state.errors).toHaveLength(1);
  });

  it("editing after a save clears the stale confirmation", () => {
    let state = reducePrepEntry(loaded, { kind: "saveStarted" });
    state = reducePrepEntry(state, { kind: "saveSucceeded", rowVersion: 4 });
    state = reducePrepEntry(state, {
      kind: "edited",
      field: "notes",
      value: "<p>x</p>",
    });
    expect(state.savedNotice).toBeNull();
  });
});
