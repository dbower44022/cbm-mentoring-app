/**
 * REQ-087 unit coverage: the zoom clamp is the contract the persisted
 * document relies on — out-of-range values (old documents, fast clicks)
 * must land inside the step range, never break rendering.
 */
import { describe, expect, it } from "vitest";
import { clampZoom, ZOOM_MAX, ZOOM_MIN } from "./panel-chrome";

describe("clampZoom", () => {
  it("keeps in-range values", () => {
    expect(clampZoom(100)).toBe(100);
    expect(clampZoom(ZOOM_MIN)).toBe(ZOOM_MIN);
    expect(clampZoom(ZOOM_MAX)).toBe(ZOOM_MAX);
  });
  it("clamps below the minimum", () => {
    expect(clampZoom(10)).toBe(ZOOM_MIN);
    expect(clampZoom(-40)).toBe(ZOOM_MIN);
  });
  it("clamps above the maximum", () => {
    expect(clampZoom(400)).toBe(ZOOM_MAX);
  });
});
