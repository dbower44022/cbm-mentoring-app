/**
 * @vitest-environment jsdom
 *
 * Interaction tests for the RENDERED run surface (WTK-094/098, REQ-042):
 * where model.test.ts pins the phase reducer and dirty-guard facts, this
 * suite drives run-panel.tsx against a stubbed fetch speaking the real
 * envelope — the once-only launch, the server's verbatim refusal on a
 * declined launch, the generic step form, cancel behind the dirty guard,
 * and the per-step Help affordance.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type WorkprocessActionPayload } from "./payloads";
import { WorkprocessRunPanel } from "./run-panel";

// React 18 warns state updates outside act() unless the environment opts in.
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ENTRY: WorkprocessActionPayload = {
  workprocessRegistrationID: "wp-reassign",
  label: "Reassign Mentor",
  description: "Moves the selected engagement to another mentor.",
  selectionContract: "single",
  classification: "modifying",
};

function ok(data: unknown): unknown {
  return { data, meta: {}, errors: null };
}

function makeRun(over: Record<string, unknown> = {}): unknown {
  return {
    workprocessRunID: "run-1",
    workprocessRegistrationID: "wp-reassign",
    runState: "inFlight",
    selectedRecordIDs: ["r1"],
    stepAnswers: {},
    currentStepKey: "chooseMentor",
    completable: false,
    rowVersion: 1,
    ...over,
  };
}

interface Exchange {
  pathname: string;
  body: unknown;
}

/** Route the run verbs; every exchange is logged for assertions. */
function renderRun(respond: (pathname: string, body: unknown) => unknown): {
  exchanges: Exchange[];
  onCompleted: () => void;
  onClose: () => void;
} {
  const exchanges: Exchange[] = [];
  vi.stubGlobal(
    "fetch",
    (input: string | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://run.test");
      // The surface always sends JSON string bodies (the envelope client's
      // replay contract) — anything else would be a defect worth failing on.
      const body: unknown =
        typeof init?.body === "string" ? JSON.parse(init.body) : null;
      exchanges.push({ pathname: url.pathname, body });
      return Promise.resolve({
        status: 200,
        json: () => Promise.resolve(respond(url.pathname, body)),
      } as unknown as Response);
    },
  );
  const onCompleted = vi.fn();
  const onClose = vi.fn();
  render(
    <WorkprocessRunPanel
      entry={ENTRY}
      dataSourceKey="engagements"
      selectedRecordIds={["r1"]}
      onCompleted={onCompleted}
      onClose={onClose}
    />,
  );
  return { exchanges, onCompleted, onClose };
}

describe("launch", () => {
  it("launches exactly once with the inherited selection", async () => {
    const { exchanges } = renderRun(() => ok(makeRun()));
    await screen.findByRole("heading", { name: "chooseMentor" });
    expect(exchanges).toEqual([
      {
        pathname: "/workprocesses/runs",
        body: {
          workprocessRegistrationID: "wp-reassign",
          dataSourceKey: "engagements",
          selectedRecordIDs: ["r1"],
        },
      },
    ]);
  });

  it("shows the server's own refusal verbatim when the launch declines", async () => {
    const { onClose } = renderRun(() => ({
      data: null,
      meta: {},
      errors: [
        {
          fieldName: "selectedRecordIDs",
          code: "selectionContractViolation",
          message:
            "'Reassign Mentor' didn't run. 'Reassign Mentor' works on exactly " +
            "one record, and 2 rows are selected. Select a single row and run " +
            "the action again.",
        },
      ],
    }));
    await screen.findByText(/'Reassign Mentor' didn't run\./);
    // Nothing pending: closing a refused launch needs no dirty guard.
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("the step walk (REQ-042)", () => {
  it("renders the author's step key verbatim, sends the answer, and commits only on Complete", async () => {
    const { exchanges, onCompleted } = renderRun((pathname) => {
      if (pathname === "/workprocesses/runs") {
        return ok(makeRun());
      }
      if (pathname.endsWith("/step")) {
        return ok(
          makeRun({
            stepAnswers: { chooseMentor: "mentor-9" },
            currentStepKey: null,
            completable: true,
          }),
        );
      }
      return ok(
        makeRun({
          runState: "committed",
          currentStepKey: null,
          confirmation: "'Reassign Mentor' completed and its changes were applied.",
        }),
      );
    });

    await screen.findByRole("heading", { name: "chooseMentor" });
    fireEvent.change(screen.getByLabelText("Answer for step 'chooseMentor'"), {
      target: { value: "mentor-9" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    // The walk resolved to a terminal step: nothing applied yet, by its own words.
    await screen.findByText(/Nothing has been applied yet/);
    expect(onCompleted).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Complete" }));
    await screen.findByText(
      "'Reassign Mentor' completed and its changes were applied.",
    );
    expect(onCompleted).toHaveBeenCalledTimes(1);
    expect(exchanges.map((e) => e.pathname)).toEqual([
      "/workprocesses/runs",
      "/workprocesses/runs/run-1/step",
      "/workprocesses/runs/run-1/commit",
    ]);
    expect(exchanges[1]?.body).toEqual({ stepKey: "chooseMentor", answer: "mentor-9" });
  });

  it("shows a frame refusal in the server's words and stays on the step", async () => {
    let refuse = true;
    renderRun((pathname) => {
      if (pathname === "/workprocesses/runs") {
        return ok(makeRun());
      }
      if (refuse) {
        refuse = false;
        return {
          data: null,
          meta: {},
          errors: [
            {
              fieldName: "stepKey",
              code: "notCurrentStep",
              message: "'other' is not the step this run is on.",
            },
          ],
        };
      }
      return ok(makeRun({ currentStepKey: "confirm" }));
    });

    await screen.findByRole("heading", { name: "chooseMentor" });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByText("'other' is not the step this run is on.");
    // Still on the step — the refusal educates, it never ends the run.
    expect(screen.getByRole("heading", { name: "chooseMentor" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: "confirm" });
  });
});

describe("leaving = cancel, behind the dirty guard (REQ-042)", () => {
  it("warns before discarding, keeps working on demand, and discards through the cancel verb", async () => {
    const { exchanges, onClose } = renderRun((pathname) => {
      if (pathname === "/workprocesses/runs") {
        return ok(makeRun({ stepAnswers: { intro: "done" } }));
      }
      if (pathname.endsWith("/cancel")) {
        return ok(makeRun({ runState: "discarded", stepAnswers: { intro: "done" } }));
      }
      throw new Error(`Unrouted: ${pathname}`);
    });
    await screen.findByRole("heading", { name: "chooseMentor" });

    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
    const guard = screen.getByRole("dialog", {
      name: "Leave 'Reassign Mentor' without finishing?",
    });
    expect(guard.textContent).toContain("Nothing has been applied");
    expect(guard.textContent).toContain(
      "Leaving discards the run and the 1 answer recorded so far.",
    );

    // Keep working: nothing sent, the step is still live.
    fireEvent.click(screen.getByRole("button", { name: "Keep working" }));
    expect(onClose).not.toHaveBeenCalled();
    expect(exchanges.map((e) => e.pathname)).toEqual(["/workprocesses/runs"]);

    // Discard: the cancel verb fires and the surface closes.
    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
    fireEvent.click(screen.getByRole("button", { name: "Discard the run" }));
    await vi.waitFor(() => {
      expect(onClose).toHaveBeenCalledTimes(1);
    });
    expect(exchanges.map((e) => e.pathname)).toEqual([
      "/workprocesses/runs",
      "/workprocesses/runs/run-1/cancel",
    ]);
  });
});

describe("per-step Help (REQ-042, REQ-043)", () => {
  it("resolves the workprocess's help through the one path and surfaces the notice", async () => {
    // The frame's Help identity is the WORKPROCESS by display name — the
    // action-list identity administrators map (SKL-122's one resolver).
    const { exchanges } = renderRun((pathname) =>
      pathname === "/help/resolve"
        ? ok({
            url: "https://docs.example.org/help",
            mapped: false,
            notice: "No page-specific help exists yet for this workprocess.",
          })
        : ok(makeRun()),
    );
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    await screen.findByRole("heading", { name: "chooseMentor" });

    fireEvent.click(screen.getByRole("button", { name: "Help" }));
    // The generic landing opens in a separate tab AND explains itself.
    const notice = await screen.findByText(/No page-specific help exists yet/);
    expect(notice.textContent).toContain("workprocess");
    expect(opened).toHaveBeenCalledWith(
      "https://docs.example.org/help",
      "_blank",
      "noopener",
    );
    expect(exchanges.map((e) => e.pathname)).toContain("/help/resolve");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/No page-specific help exists yet/)).toBeNull();
  });
});
