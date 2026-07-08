/**
 * @vitest-environment jsdom
 *
 * The relationship lookup control (REQ-036): registry-driven type-ahead over
 * the one suggestion read, phases rendered verbatim (keep-typing, matches
 * with server-side count, no-access explains — never hides), the FK-only
 * value write, and the two always-visible inline affordances.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type FormFieldPayload } from "../api/payloads";
import { LookupControl, relatedEntityType } from "./lookup-control";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const FIELD: FormFieldPayload = {
  fieldName: "progressGoalID",
  fieldLabel: "Progress goal",
  fieldType: "reference",
  requiredFlag: false,
  validationRules: null,
  defaultValue: null,
  helpText: null,
  visibilityHints: null,
  optionSet: null,
  editable: true,
  readOnly: null,
  help: null,
};

function renderLookup(value: unknown = null): {
  onChange: ReturnType<typeof vi.fn>;
} {
  const onChange = vi.fn();
  render(
    <LookupControl
      entityType="mentor"
      field={FIELD}
      value={value}
      invalid={false}
      onChange={onChange}
      onExit={vi.fn()}
    />,
  );
  return { onChange };
}

function envelope(body: unknown): Response {
  return { status: 200, json: () => Promise.resolve(body) } as unknown as Response;
}

describe("the lookup control (REQ-036)", () => {
  it("derives the related entity from the entity-named key (DB-R2/R2b)", () => {
    expect(relatedEntityType("progressGoalID")).toBe("progressGoal");
    expect(relatedEntityType("crmEngagementRefID")).toBe("crmEngagementRef");
    expect(() => relatedEntityType("name")).toThrow(/not an entity-named reference/);
  });

  it("queries the one suggestion read per keystroke and writes only the FK", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      envelope({
        data: {
          phase: "matches",
          suggestions: [
            { entityType: "progressGoal", recordId: "g-1", title: "Improve budgeting" },
          ],
          totalMatches: 1,
          summary: "1 match",
          message: null,
        },
        meta: {},
        errors: null,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { onChange } = renderLookup();
    const input = screen.getByRole("combobox", { name: "Progress goal" });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "bud" } });
    const [path] = fetchMock.mock.calls[0] as [string];
    expect(path).toBe("/lookups/mentor/progressGoalID?q=bud");
    const option = await screen.findByRole("option", { name: "Improve budgeting" });
    fireEvent.mouseDown(option);
    // The form writes the record ID only — the title never round-trips.
    expect(onChange).toHaveBeenCalledWith("g-1");
  });

  it("renders the no-access phase as an explanation — the field never hides", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      envelope({
        data: {
          phase: "noAccess",
          suggestions: [],
          totalMatches: 0,
          summary: null,
          message: {
            whatHappened: "Progress goal suggestions aren't available.",
            why: "Your roles don't cover the data source.",
            whatNext: "Ask an administrator for access.",
          },
        },
        meta: {},
        errors: null,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderLookup();
    const input = screen.getByRole("combobox", { name: "Progress goal" });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "budget" } });
    expect(
      await screen.findByText("Progress goal suggestions aren't available."),
    ).toBeTruthy();
    // The control itself stays rendered and enabled.
    expect(screen.getByRole("combobox", { name: "Progress goal" })).toBeTruthy();
  });

  it("Open with nothing linked explains instead of disabling", () => {
    renderLookup(null);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(screen.getByText("'Open' didn't run.")).toBeTruthy();
    expect(screen.getByText("No Progress goal is linked yet.")).toBeTruthy();
  });

  it("Open on a linked record raises the pinned pop-out window", () => {
    const opened = { focus: vi.fn() };
    const openMock = vi.fn().mockReturnValue(opened);
    vi.stubGlobal("open", openMock);
    renderLookup("g-9");
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(openMock).toHaveBeenCalledWith(
      "/records/progressGoal/g-9",
      "record:progressGoal:g-9",
      "popup=yes,width=1000,height=800",
    );
  });

  it("New… opens the related entity's standard create form", () => {
    const openMock = vi.fn().mockReturnValue(null);
    vi.stubGlobal("open", openMock);
    renderLookup();
    fireEvent.click(screen.getByRole("button", { name: "New…" }));
    expect(openMock).toHaveBeenCalledWith(
      "/records/progressGoal/new",
      "_blank",
      "popup=yes,width=1000,height=800",
    );
  });

  it("clearing the text clears the link", () => {
    const fetchMock = vi.fn().mockResolvedValue(
      envelope({
        data: {
          phase: "idle",
          suggestions: [],
          totalMatches: 0,
          summary: null,
          message: null,
        },
        meta: {},
        errors: null,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { onChange } = renderLookup("g-9");
    const input = screen.getByRole("combobox", { name: "Progress goal" });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "x" } });
    fireEvent.change(input, { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(null);
  });
});
