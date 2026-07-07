/**
 * The once-per-window-boot startup landing (FND-909 D8, REQ-072): the latch
 * fires exactly once per boot, and the navigation decision only hijacks a
 * non-Home target from the boot route "/". Pure logic — the Shell wires the
 * window latch; tests use fresh instances.
 */

import { describe, expect, it } from "vitest";

import { createStartupLatch, startupNavigation } from "./startup";

describe("createStartupLatch", () => {
  it("claims exactly once; every later claim is refused", () => {
    const latch = createStartupLatch();
    expect(latch()).toBe(true);
    expect(latch()).toBe(false);
    expect(latch()).toBe(false);
  });

  it("is per-instance: one boot's claim never leaks into another latch", () => {
    const first = createStartupLatch();
    const second = createStartupLatch();
    expect(first()).toBe(true);
    expect(second()).toBe(true);
  });
});

describe("startupNavigation", () => {
  it("redirects a non-Home startup target from the boot route", () => {
    expect(startupNavigation("engagements", "home", "/")).toBe("/panel/engagements");
  });

  it("stays put when the startup target IS Home", () => {
    expect(startupNavigation("home", "home", "/")).toBeNull();
  });

  it("never hijacks a deep-linked window off its own destination", () => {
    expect(startupNavigation("engagements", "home", "/panel/resources")).toBeNull();
    expect(startupNavigation("engagements", "home", "/prep/abc")).toBeNull();
  });

  it("URL-encodes the panel key it routes to", () => {
    expect(startupNavigation("a b", "home", "/")).toBe("/panel/a%20b");
  });
});
