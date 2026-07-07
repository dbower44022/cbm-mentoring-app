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
    externalMeetingID: null,
    transcriptSource: null,
    draftSummary: null,
    draftActionItems: null,
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

describe("the REQ-083 proposals", () => {
  const withDrafts = loadedEntry(
    sessionAt("s-2", "2026-07-10T15:00:00Z", {
      sessionNotes: "<p>Typed already</p>",
      draftSummary: "<p>Drafted summary</p>",
      draftActionItems: "<ul><li>Drafted item</li></ul>",
      rowVersion: 5,
    }),
  );

  it("loads the standing proposals without touching the entry", () => {
    expect(withDrafts.draftSummary).toBe("<p>Drafted summary</p>");
    expect(withDrafts.notes).toBe("<p>Typed already</p>");
    expect(isDirty(withDrafts)).toBe(false);
  });

  it("inserting a draft APPENDS to the typed entry and bumps the revision", () => {
    const inserted = reducePrepEntry(withDrafts, {
      kind: "draftInserted",
      field: "notes",
    });
    // The mentor's own words survive; the draft joins them (never replaces),
    // and only saving persists anything — author of record.
    expect(inserted.notes).toBe("<p>Typed already</p><p>Drafted summary</p>");
    expect(inserted.notesRevision).toBe(1);
    expect(inserted.actionItemsRevision).toBe(0);
    expect(isDirty(inserted)).toBe(true);
    expect(inserted.savedNotes).toBe("<p>Typed already</p>");
  });

  it("inserting with no standing draft is a no-op", () => {
    const bare = loadedEntry(sessionAt("s-3", "2026-07-10T15:00:00Z"));
    expect(reducePrepEntry(bare, { kind: "draftInserted", field: "notes" })).toBe(bare);
  });

  it("an external update re-arms rowVersion and drafts, never the typed text", () => {
    const typed = reducePrepEntry(withDrafts, {
      kind: "edited",
      field: "notes",
      value: "<p>Mid-edit</p>",
    });
    const updated = reducePrepEntry(typed, {
      kind: "externalUpdate",
      rowVersion: 6,
      draftSummary: "<p>New draft</p>",
      draftActionItems: null,
      notice: "Transcript retrieved.",
    });
    expect(updated.rowVersion).toBe(6);
    expect(updated.draftSummary).toBe("<p>New draft</p>");
    expect(updated.draftActionItems).toBeNull();
    expect(updated.notes).toBe("<p>Mid-edit</p>");
    expect(updated.savedNotice).toBe("Transcript retrieved.");
  });
});
