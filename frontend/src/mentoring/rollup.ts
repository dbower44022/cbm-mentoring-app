/**
 * The REQ-088 fill model, pure (WTK-232, Doug's 2026-07-06 ruling): the
 * engagement preview's notes/action-items rollup FILLS the available
 * vertical space — never a fixed truncation, never idle white space while
 * more rollup exists — with a continues-indicator when more remains. The
 * reference behavior is prototype/app.js renderPreview's fill pass: start
 * from a minimum, grow one entry at a time until the pane is visually full,
 * re-fill on resize. This module is the pure step function that loop drives
 * (and the vitest target); the component owns only DOM measurement.
 */

/** The pre-measurement floor the prototype's fill pass starts from. */
export const INITIAL_ROLLUP_COUNT = 2;

export interface FillState {
  /** How many rollup entries are currently rendered. */
  shown: number;
  /** How many exist in total. */
  total: number;
}

/** The starting state for one fill pass (initial render or a re-fill). */
export function initialFill(total: number): FillState {
  return { shown: Math.min(INITIAL_ROLLUP_COUNT, Math.max(0, total)), total };
}

/**
 * One measurement step: the component measured the pane after the last
 * render and reports whether free space remains (`fits` — content height
 * does not exceed the pane). Grow by exactly one entry while space remains
 * and more exists; otherwise the pass has converged. Growing one at a time
 * is what makes the loop terminate at "visually full" instead of
 * overshooting past it.
 */
export function grow(state: FillState, fits: boolean): FillState {
  if (!fits || state.shown >= state.total) {
    return state;
  }
  return { ...state, shown: state.shown + 1 };
}

/** Whether another measurement round is needed after rendering `state`. */
export function fillSettled(state: FillState, fits: boolean): boolean {
  return !fits || state.shown >= state.total;
}

/**
 * The continues-indicator (REQ-088): when more rollup exists than fits,
 * say so and say where the full rollup lives — never a silent cut.
 */
export function continuesIndicator(state: FillState): string | null {
  const remaining = state.total - state.shown;
  if (remaining <= 0) {
    return null;
  }
  const sessions =
    remaining === 1 ? "1 more session" : `${String(remaining)} more sessions`;
  return `…rollup continues (${sessions}) — full rollup on the session prep surface.`;
}
