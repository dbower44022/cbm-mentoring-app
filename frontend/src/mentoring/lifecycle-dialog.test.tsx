/**
 * @vitest-environment jsdom
 *
 * The accept-assignment flow wiring (WTK-188, REQ-075/076): the lifecycle
 * dialog reads the engagement truthfully (name + current status + the
 * rowVersion the write must carry), confirms before anything moves, POSTs
 * the transition, refreshes the launching grid the moment the flip lands,
 * surfaces the REQ-076 next steps in place, and renders the server's
 * educate refusal verbatim when the status disallows the transition.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LifecycleDialog } from "./lifecycle-dialog";
import { type EngagementRollupPayload } from "./payloads";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function ok(data: unknown): unknown {
  return { data, meta: {}, errors: null };
}

function rollup(): EngagementRollupPayload {
  return {
    engagement: {
      engagementID: "e-1",
      engagementName: "Acme Growth",
      engagementStatus: "pendingAcceptance",
      engagementStatusLabel: "Pending Acceptance",
      engagementSummary: null,
      primaryContactName: "Sam Contact",
      primaryContactEmail: "sam@acme.example",
      primaryContactCrmID: null,
      crmEngagementID: null,
      rowVersion: 7,
    },
    client: null,
    contacts: [],
    stats: {
      totalSessions: 0,
      heldSessions: 0,
      firstSessionAt: null,
      lastSessionAt: null,
      nextSessionAt: null,
    },
    rollup: [],
    sessions: [],
  };
}

const ACCEPT = {
  key: "acceptAssignment",
  label: "Accept Assignment",
  selectionContract: "single",
  classification: "modifying",
} as const;

interface Exchange {
  method: string;
  pathname: string;
  body: unknown;
}

function renderDialog(
  respond: (exchange: Exchange) => { status: number; body: unknown },
): {
  exchanges: Exchange[];
  onCompleted: ReturnType<typeof vi.fn>;
  onClose: ReturnType<typeof vi.fn>;
} {
  const exchanges: Exchange[] = [];
  vi.stubGlobal(
    "fetch",
    (input: string | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://lifecycle.test");
      const exchange: Exchange = {
        method: init?.method ?? "GET",
        pathname: url.pathname,
        body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
      };
      exchanges.push(exchange);
      const answer = respond(exchange);
      return Promise.resolve({
        status: answer.status,
        json: () => Promise.resolve(answer.body),
      } as unknown as Response);
    },
  );
  const onCompleted = vi.fn();
  const onClose = vi.fn();
  render(
    <LifecycleDialog
      action={ACCEPT}
      transition="accept"
      engagementId="e-1"
      onCompleted={onCompleted}
      onClose={onClose}
    />,
  );
  return { exchanges, onCompleted, onClose };
}

describe("the accept flow", () => {
  it("confirms with the engagement's own name and status before anything moves", async () => {
    const { exchanges } = renderDialog(() => ({ status: 200, body: ok(rollup()) }));
    await screen.findByText("Acme Growth");
    expect(screen.getByText("Pending Acceptance")).toBeDefined();
    // Only the rollup READ has run — no POST until Continue.
    expect(exchanges.every((e) => e.method === "GET")).toBe(true);
  });

  it("POSTs the transition with the read rowVersion, refreshes, and offers the next steps", async () => {
    const { exchanges, onCompleted } = renderDialog((exchange) => {
      if (exchange.method === "POST") {
        return {
          status: 200,
          body: ok({
            engagement: { ...rollup().engagement, engagementStatus: "assigned" },
            transition: "accept",
            confirmation: "'Acme Growth' accepted — its status is now Assigned.",
            nextSteps: [
              {
                key: "sendIntroEmail",
                label: "Send the introduction email",
                templateKey: "mentorIntroduction",
              },
              { key: "scheduleFirstSession", label: "Schedule the first session" },
            ],
          }),
        };
      }
      return { status: 200, body: ok(rollup()) };
    });
    fireEvent.click(await screen.findByRole("button", { name: "Continue" }));
    await screen.findByText("'Acme Growth' accepted — its status is now Assigned.");

    const post = exchanges.find((e) => e.method === "POST");
    expect(post).toEqual({
      method: "POST",
      pathname: "/engagements/e-1/lifecycle",
      body: { transition: "accept", rowVersion: 7 },
    });
    // The flip is already real: the grid refreshed on landing, not on Close.
    expect(onCompleted).toHaveBeenCalledTimes(1);
    // REQ-076: both post-acceptance steps offered in place.
    expect(
      screen.getByRole("button", { name: "Send the introduction email" }),
    ).toBeDefined();
    expect(
      screen.getByRole("button", { name: "Schedule the first session" }),
    ).toBeDefined();
  });

  it("opens the schedule form from the next step and posts the session", async () => {
    renderDialog((exchange) => {
      if (exchange.method === "POST" && exchange.pathname.endsWith("/lifecycle")) {
        return {
          status: 200,
          body: ok({
            engagement: rollup().engagement,
            transition: "accept",
            confirmation: "accepted",
            nextSteps: [
              { key: "scheduleFirstSession", label: "Schedule the first session" },
            ],
          }),
        };
      }
      if (exchange.method === "POST" && exchange.pathname.endsWith("/sessions")) {
        return {
          status: 200,
          body: ok({ sessionID: "s-1", rowVersion: 1 }),
        };
      }
      return { status: 200, body: ok(rollup()) };
    });
    fireEvent.click(await screen.findByRole("button", { name: "Continue" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Schedule the first session" }),
    );
    fireEvent.change(screen.getByLabelText("Session date and time"), {
      target: { value: "2026-07-14T10:00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Schedule Session" }));
    await screen.findByText(/Session scheduled/);
  });

  it("renders the server's educate refusal verbatim and applies nothing", async () => {
    const { onCompleted } = renderDialog((exchange) => {
      if (exchange.method === "POST") {
        return {
          status: 422,
          body: {
            data: null,
            meta: {},
            errors: [
              {
                fieldName: "transition",
                code: "invalidLifecycleTransition",
                message:
                  "Accept Assignment ran on 'Acme Growth' and didn't apply. Its status is 'Active'.",
              },
            ],
          },
        };
      }
      return { status: 200, body: ok(rollup()) };
    });
    fireEvent.click(await screen.findByRole("button", { name: "Continue" }));
    await screen.findByText(/didn't apply/);
    expect(onCompleted).not.toHaveBeenCalled();
    // The confirm step stays available — the dialog educates, never traps.
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDefined();
  });
});
