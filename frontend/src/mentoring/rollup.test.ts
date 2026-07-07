/**
 * The REQ-088 fill model (WTK-188/232): the rollup fills available space —
 * grow one entry per measured render while space remains, converge at
 * "visually full", continues-indicator when more exists, and a re-fill
 * restarts from the floor. Mirrors prototype/app.js renderPreview's fill
 * pass, the ruled reference implementation.
 */

import { describe, expect, it } from "vitest";

import {
  continuesIndicator,
  fillSettled,
  grow,
  INITIAL_ROLLUP_COUNT,
  initialFill,
} from "./rollup";

describe("initialFill", () => {
  it("starts at the floor, clamped to what exists", () => {
    expect(initialFill(10)).toEqual({ shown: INITIAL_ROLLUP_COUNT, total: 10 });
    expect(initialFill(1)).toEqual({ shown: 1, total: 1 });
    expect(initialFill(0)).toEqual({ shown: 0, total: 0 });
  });
});

describe("grow", () => {
  it("adds exactly one entry while space remains and more exists", () => {
    expect(grow({ shown: 2, total: 5 }, true)).toEqual({ shown: 3, total: 5 });
  });

  it("stops at the pane edge — a full pane never grows", () => {
    const state = { shown: 3, total: 5 };
    expect(grow(state, false)).toBe(state);
  });

  it("stops when everything is shown, whatever the space", () => {
    const state = { shown: 5, total: 5 };
    expect(grow(state, true)).toBe(state);
  });
});

describe("the fill loop convergence", () => {
  it("fills exactly to the space a pane offers", () => {
    // A pane that fits 4 entries: fits reads true until the 5th renders.
    const fitsUpTo = (capacity: number) => (state: { shown: number }) =>
      state.shown <= capacity;
    let state = initialFill(10);
    const fits = fitsUpTo(4);
    while (!fillSettled(state, fits(state))) {
      state = grow(state, fits(state));
    }
    // Converges one past capacity — the entry that made it visually full.
    expect(state.shown).toBe(5);
    expect(continuesIndicator(state)).toContain("5 more sessions");
  });

  it("shows everything when the pane is tall enough — no indicator", () => {
    let state = initialFill(3);
    while (!fillSettled(state, true)) {
      state = grow(state, true);
    }
    expect(state.shown).toBe(3);
    expect(continuesIndicator(state)).toBeNull();
  });
});

describe("continuesIndicator", () => {
  it("says how much more exists and where the full rollup lives", () => {
    expect(continuesIndicator({ shown: 2, total: 5 })).toBe(
      "…rollup continues (3 more sessions) — full rollup on the session prep surface.",
    );
    expect(continuesIndicator({ shown: 4, total: 5 })).toContain("1 more session");
  });

  it("is silent when nothing is cut", () => {
    expect(continuesIndicator({ shown: 5, total: 5 })).toBeNull();
    expect(continuesIndicator({ shown: 0, total: 0 })).toBeNull();
  });
});
