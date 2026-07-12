/**
 * @vitest-environment jsdom
 *
 * The session details surface (REQ-110, PI-015): the one-read render
 * (identity band, context bar, attendee grid, long-form), the on-demand
 * transcript (fetched ONLY when attached; find counts matches), the
 * educate states for expected/unavailable transcripts, the never-disabled
 * conference control, and the copy tools' clipboard payloads. The pure
 * grid/TSV/find helpers are pinned alongside.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type SessionDetailPayload } from "./payloads";
import {
  attendeeEmails,
  attendeeGridTsv,
  SessionDetailPreview,
  transcriptMatchCount,
} from "./session-detail";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function ok(data: unknown): unknown {
  return { data, meta: {}, errors: null };
}

const ATTENDEES = [
  {
    name: "frank@example.org",
    role: "mentor",
    companyName: null,
    companyRefID: null,
    crmContactID: "mentor-frank",
    email: null,
    phone: "(216) 555-1042",
    participation: "attended",
  },
  {
    name: "Sam Contact",
    role: "client",
    companyName: "acct-Alpha",
    companyRefID: "11111111-1111-1111-1111-111111111111",
    crmContactID: "crm-contact-1",
    email: "sam@acme.example",
    phone: "(216) 555-2087",
    participation: "attended",
  },
];

function detail(
  transcript: SessionDetailPayload["transcript"],
  conferenceLink: string | null = "https://meet.example/abc",
): SessionDetailPayload {
  return {
    session: {
      sessionID: "22222222-2222-2222-2222-222222222222",
      scheduledAt: "2026-07-09T18:00:00+00:00",
      sessionStatus: "completed",
      sessionStatusLabel: "Completed",
      conferenceLink,
      sessionNotes: "<p>Reviewed the cash-flow worksheet.</p>",
      actionItems: "<ul><li>Bring the June P&amp;L</li></ul>",
      externalMeetingID: null,
      transcriptSource: transcript.source,
      draftSummary: null,
      draftActionItems: null,
      rowVersion: 7,
    },
    engagement: {
      engagementID: "33333333-3333-3333-3333-333333333333",
      engagementName: "Alpha — Acme Growth",
    },
    client: {
      clientID: "44444444-4444-4444-4444-444444444444",
      crmCompanyRefID: "11111111-1111-1111-1111-111111111111",
      crmCompanyID: "acct-Alpha",
    },
    attendees: ATTENDEES,
    transcript,
  };
}

function stubFetch(
  payload: SessionDetailPayload,
  transcriptText: string | null,
): string[] {
  const paths: string[] = [];
  vi.stubGlobal("fetch", (input: string | URL): Promise<Response> => {
    const pathname = new URL(String(input), "http://detail.test").pathname;
    paths.push(pathname);
    const body = pathname.endsWith("/transcript")
      ? ok({
          state: payload.transcript.state,
          transcriptText,
          transcriptSource: payload.transcript.source,
        })
      : ok(payload);
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
  return paths;
}

function stubClipboard(): ReturnType<typeof vi.fn> {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(globalThis.navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
  return writeText;
}

describe("SessionDetailPreview", () => {
  it("renders identity, context, and the attendee grid from the one read", async () => {
    stubFetch(detail({ state: "unavailable", source: null, wordCount: 0 }), null);
    render(<SessionDetailPreview recordId="s-1" />);

    expect(await screen.findByText(/^Session — /)).toBeTruthy();
    expect(screen.getByText("Completed")).toBeTruthy();
    expect(screen.getByText("Alpha — Acme Growth")).toBeTruthy();

    const grid = screen.getByRole("table");
    expect(grid.textContent).toContain("Sam Contact");
    expect(grid.textContent).toContain("Mentor");
    expect(grid.textContent).toContain("(216) 555-2087");
    expect(screen.getAllByText("Attended").length).toBe(2);
  });

  it("educates instead of fetching when no transcript can be retrieved", async () => {
    const paths = stubFetch(
      detail({ state: "unavailable", source: null, wordCount: 0 }),
      null,
    );
    render(<SessionDetailPreview recordId="s-1" />);

    expect(await screen.findByText(/can't be retrieved automatically/)).toBeTruthy();
    expect(screen.getByText(/Paste or upload the transcript/)).toBeTruthy();
    expect(paths.some((p) => p.endsWith("/transcript"))).toBe(false);
  });

  it("shows the retries-automatically message while a transcript is expected", async () => {
    stubFetch(detail({ state: "expected", source: null, wordCount: 0 }), null);
    render(<SessionDetailPreview recordId="s-1" />);

    expect(await screen.findByText(/retrieval retries automatically/)).toBeTruthy();
  });

  it("loads the transcript on demand and finds matches", async () => {
    const paths = stubFetch(
      detail({ state: "attached", source: "platform", wordCount: 6 }),
      "pricing talk then more pricing talk",
    );
    // jsdom has no scrollIntoView; the find-jump calls it on the first mark.
    Object.assign(Element.prototype, { scrollIntoView: vi.fn() });
    render(<SessionDetailPreview recordId="s-1" />);

    expect(await screen.findByText(/From the meeting platform · 6 words/)).toBeTruthy();
    expect(paths.some((p) => p.endsWith("/transcript"))).toBe(true);

    fireEvent.change(screen.getByLabelText("Find in transcript"), {
      target: { value: "pricing" },
    });
    expect(await screen.findByText("2 matches")).toBeTruthy();
  });

  it("keeps the conference control clickable and educates without a link", async () => {
    stubFetch(detail({ state: "unavailable", source: null, wordCount: 0 }, null), null);
    render(<SessionDetailPreview recordId="s-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Join conference" }));
    expect(screen.getByText(/no conference to join yet/i)).toBeTruthy();
  });

  it("copies the grid as TSV with headers", async () => {
    stubFetch(detail({ state: "unavailable", source: null, wordCount: 0 }), null);
    const writeText = stubClipboard();
    render(<SessionDetailPreview recordId="s-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "⧉ Copy grid" }));
    const tsv = writeText.mock.calls[0]?.[0] as string;
    expect(tsv.split("\n")[0]).toBe("Name\tRole\tCompany\tEmail\tPhone\tStatus");
    expect(tsv).toContain("Sam Contact\tClient\tacct-Alpha\tsam@acme.example");
  });
});

describe("pure helpers", () => {
  it("drops blank emails from the compose list", () => {
    expect(attendeeEmails(ATTENDEES)).toBe("sam@acme.example");
  });

  it("renders every attendee as one TSV row", () => {
    expect(attendeeGridTsv(ATTENDEES).split("\n")).toHaveLength(3);
  });

  it("counts matches case-insensitively and answers zero for empty needles", () => {
    expect(transcriptMatchCount("Pricing and pricing", "pricing")).toBe(2);
    expect(transcriptMatchCount("anything", "")).toBe(0);
  });
});
