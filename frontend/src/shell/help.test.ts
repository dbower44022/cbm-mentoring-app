/**
 * @vitest-environment jsdom
 *
 * Tests for the ONE help resolution path (WTK-109, SKL-122, REQ-043):
 * openHelp resolves the surface through GET /help/resolve, opens the URL in
 * a separate tab (never navigating the working window), surfaces the
 * server's educate notice through the caller's sink, and explains itself —
 * never silently dead-ends — when the resolver can't be reached.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { HELP_UNREACHABLE_NOTICE, type HelpResolution, openHelp } from "./help";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function stubResolve(resolution: HelpResolution): { calls: URL[] } {
  const calls: URL[] = [];
  vi.stubGlobal("fetch", (input: string | URL): Promise<Response> => {
    calls.push(new URL(String(input), "http://help.test"));
    return Promise.resolve({
      status: 200,
      json: () => Promise.resolve({ data: resolution, meta: {}, errors: null }),
    } as unknown as Response);
  });
  return { calls };
}

describe("openHelp (the one resolver)", () => {
  it("opens a mapped page's URL in a separate noopener tab, with no notice", async () => {
    const { calls } = stubResolve({
      url: "https://docs.example.org/help/panels/engagements",
      mapped: true,
      notice: null,
    });
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    const onNotice = vi.fn();

    await openHelp("panel", "engagements", onNotice);

    // The resolve request carries the surface's own coordinates.
    const request = calls[0];
    expect(request?.pathname).toBe("/help/resolve");
    expect(request?.searchParams.get("sourceType")).toBe("panel");
    expect(request?.searchParams.get("sourceIdentifier")).toBe("engagements");
    // Separate window/tab, never a navigation; noopener severs the handle.
    expect(opened).toHaveBeenCalledWith(
      "https://docs.example.org/help/panels/engagements",
      "_blank",
      "noopener",
    );
    expect(onNotice).not.toHaveBeenCalled();
  });

  it("opens the home fallback AND surfaces the server's educate notice", async () => {
    stubResolve({
      url: "https://docs.example.org/help",
      mapped: false,
      notice:
        "No page-specific help exists yet for this panel — this opens the help site's home.",
    });
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    const onNotice = vi.fn();

    await openHelp("panel", "unmappedPanel", onNotice);

    expect(opened).toHaveBeenCalledWith(
      "https://docs.example.org/help",
      "_blank",
      "noopener",
    );
    // The server's words, verbatim, through the caller's notice mechanism.
    expect(onNotice).toHaveBeenCalledWith(
      "No page-specific help exists yet for this panel — this opens the help site's home.",
    );
  });

  it("surfaces the notice WITHOUT opening anything when help is unconfigured", async () => {
    stubResolve({
      url: null,
      mapped: false,
      notice: "Help isn't set up yet: no help site is configured for this app.",
    });
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    const onNotice = vi.fn();

    await openHelp("dataSet", "engagements", onNotice);

    // No URL means nothing to open — a blank tab would be the dead end
    // REQ-043 forbids; the notice IS the answer.
    expect(opened).not.toHaveBeenCalled();
    expect(onNotice).toHaveBeenCalledWith(
      "Help isn't set up yet: no help site is configured for this app.",
    );
  });

  it("explains an unreachable resolver instead of dead-ending silently", async () => {
    vi.stubGlobal("fetch", (): Promise<Response> =>
      Promise.reject(new Error("network down")),
    );
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    const onNotice = vi.fn();

    await openHelp("workprocess", "Bulk Reassign Mentor", onNotice);

    expect(opened).not.toHaveBeenCalled();
    expect(onNotice).toHaveBeenCalledWith(HELP_UNREACHABLE_NOTICE);
  });
});
