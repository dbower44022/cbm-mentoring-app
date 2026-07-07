/**
 * @vitest-environment jsdom
 *
 * The compose-from-template dialog (WTK-188, REQ-076/077): the staff
 * template list drives the choose step, Preview is a round trip that sends
 * NOTHING (`confirmed: false`), the merged message renders for review, and
 * Send re-posts the same prepare with `confirmed: true`. Refusals render
 * the server's own words. The pure flow reducer is pinned alongside.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CHOOSING, reduceCompose } from "./compose-model";
import { ComposeEmailDialog } from "./compose-email";
import { type EmailSendPayload } from "./payloads";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function ok(data: unknown): unknown {
  return { data, meta: {}, errors: null };
}

const TEMPLATES = [
  {
    templateKey: "mentorIntroduction",
    templateName: "Mentor introduction (post-acceptance)",
    mergeFields: [],
  },
  {
    templateKey: "sessionFollowUp",
    templateName: "Session follow-up & action items",
    mergeFields: [],
  },
];

function preview(sent: boolean): EmailSendPayload {
  return {
    templateKey: "mentorIntroduction",
    to: { address: "sam@acme.example", name: "Sam Contact" },
    subject: "Introduction from your CBM mentor — Acme Growth",
    body: "Hello Sam Contact,\n\nMy name is casey@example.org…",
    sent,
    confirmation: sent ? "The email was sent to sam@acme.example." : null,
  };
}

interface Exchange {
  method: string;
  pathname: string;
  body: unknown;
}

function renderDialog(
  respond: (exchange: Exchange) => { status: number; body: unknown },
): { exchanges: Exchange[]; onClose: ReturnType<typeof vi.fn> } {
  const exchanges: Exchange[] = [];
  vi.stubGlobal(
    "fetch",
    (input: string | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://compose.test");
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
  const onClose = vi.fn();
  render(<ComposeEmailDialog engagementId="e-1" onClose={onClose} />);
  return { exchanges, onClose };
}

describe("the compose dialog", () => {
  it("offers the staff-maintained template list and previews without sending", async () => {
    const { exchanges } = renderDialog((exchange) => {
      if (exchange.pathname === "/email/templates") {
        return { status: 200, body: ok(TEMPLATES) };
      }
      return { status: 200, body: ok(preview(false)) };
    });
    fireEvent.change(await screen.findByLabelText("Template"), {
      target: { value: "mentorIntroduction" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));

    // The merged message renders for review, server copy verbatim.
    await screen.findByText("Introduction from your CBM mentor — Acme Growth");
    expect(screen.getByText(/Nothing has been sent/)).toBeDefined();
    const post = exchanges.find((e) => e.method === "POST");
    expect(post?.body).toEqual({
      templateKey: "mentorIntroduction",
      engagementID: "e-1",
      confirmed: false,
    });
  });

  it("Send re-posts the same prepare confirmed and shows the confirmation", async () => {
    const { exchanges } = renderDialog((exchange) => {
      if (exchange.pathname === "/email/templates") {
        return { status: 200, body: ok(TEMPLATES) };
      }
      const confirmed =
        exchange.body !== null && (exchange.body as { confirmed: boolean }).confirmed;
      return { status: 200, body: ok(preview(confirmed)) };
    });
    fireEvent.change(await screen.findByLabelText("Template"), {
      target: { value: "mentorIntroduction" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    fireEvent.click(await screen.findByRole("button", { name: "Send" }));

    await screen.findByText("The email was sent to sam@acme.example.");
    const posts = exchanges.filter((e) => e.method === "POST");
    expect(posts.map((e) => (e.body as { confirmed: boolean }).confirmed)).toEqual([
      false,
      true,
    ]);
  });

  it("renders the server's refusal in its own words", async () => {
    renderDialog((exchange) => {
      if (exchange.pathname === "/email/templates") {
        return { status: 200, body: ok(TEMPLATES) };
      }
      return {
        status: 422,
        body: {
          data: null,
          meta: {},
          errors: [
            {
              fieldName: "engagementID",
              code: "noContactEmail",
              message: "'Acme Growth' has no primary contact email on record.",
            },
          ],
        },
      };
    });
    fireEvent.change(await screen.findByLabelText("Template"), {
      target: { value: "mentorIntroduction" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByText(/no primary contact email/);
  });
});

describe("the flow reducer", () => {
  it("walks choose → preview → sent", () => {
    let state = reduceCompose(CHOOSING, { kind: "previewRequested" });
    expect(state).toEqual({ phase: "choosing", busy: true, errors: null });
    state = reduceCompose(state, { kind: "previewArrived", preview: preview(false) });
    expect(state.phase).toBe("previewing");
    state = reduceCompose(state, { kind: "sendRequested" });
    state = reduceCompose(state, { kind: "sendSucceeded", result: preview(true) });
    expect(state).toEqual({ phase: "sent", result: preview(true) });
  });

  it("a refusal lands on the phase it interrupted; Back restarts clean", () => {
    const previewing = reduceCompose(CHOOSING, {
      kind: "previewArrived",
      preview: preview(false),
    });
    const refused = reduceCompose(previewing, { kind: "refused", errors: [] });
    expect(refused.phase).toBe("previewing");
    expect(reduceCompose(refused, { kind: "backToChoosing" })).toEqual(CHOOSING);
    const chooseRefused = reduceCompose(CHOOSING, { kind: "refused", errors: null });
    expect(chooseRefused).toEqual({ phase: "choosing", busy: false, errors: [] });
  });
});
