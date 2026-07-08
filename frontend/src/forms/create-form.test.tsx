/**
 * @vitest-environment jsdom
 *
 * The create form (REQ-037, REL-004 block 1): the same full-screen form
 * opened empty and seeded from field-setting defaults; the non-blocking
 * similar-records offer (advisory on identity-field exit, ENFORCED with the
 * recorded override on the server's 409); switch-to-existing and
 * restore-instead choices; first save lands on the read view; Cancel
 * creates nothing.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type CreateFormPayload, type FormFieldPayload } from "../api/payloads";
import { CreateFormScreen } from "./create-form";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function field(
  name: string,
  overrides: Partial<FormFieldPayload> = {},
): FormFieldPayload {
  return {
    fieldName: name,
    fieldLabel: name,
    fieldType: "text",
    requiredFlag: false,
    validationRules: null,
    defaultValue: null,
    helpText: null,
    optionSet: null,
    editable: true,
    readOnly: null,
    help: null,
    ...overrides,
  };
}

function payload(overrides: Partial<CreateFormPayload> = {}): CreateFormPayload {
  return {
    screen: {
      presentation: "fullScreen",
      fieldPositions: "matchesReadView",
      controlScale: "scaledUpForEditControls",
      initialFocus: "firstEditableField",
      escape: "requestLeave",
      save: { label: "Save", prominence: "large", shortcut: "Ctrl+S" },
      cancel: { label: "Cancel", behavior: "revertToOriginal" },
    },
    keyboard: {
      save: "Ctrl+S",
      escapeFullForm: "requestLeave",
      escapePerFieldWindow: "cancel",
      enter: "activateFocusedControl",
      tab: "nextEditableField",
      shiftTab: "previousEditableField",
      initialFocus: "firstEditableField",
    },
    form: {
      kind: "fullScreenForm",
      opens: "empty",
      prefillSource: "defaultValue",
      validation: "sharedFormEngine",
      similarCheck: "nonBlocking",
      comparison: "sideBySide",
      commits: "postCreate",
      landsOn: "readView",
      cancelCreates: "nothing",
    },
    fields: [
      field("mentorName", { fieldLabel: "Name", requiredFlag: true }),
      field("mentorNote", { fieldLabel: "Note" }),
    ],
    seed: { mentorNote: "welcome" },
    identityFieldNames: ["mentorName"],
    initialFocusField: "mentorName",
    ...overrides,
  };
}

function renderCreate(p: CreateFormPayload = payload()): {
  onCreated: ReturnType<typeof vi.fn>;
  onLeave: ReturnType<typeof vi.fn>;
} {
  const onCreated = vi.fn();
  const onLeave = vi.fn();
  render(
    <CreateFormScreen
      entityType="mentor"
      payload={p}
      onCreated={onCreated}
      onLeave={onLeave}
    />,
  );
  return { onCreated, onLeave };
}

function envelope(status: number, body: unknown): Response {
  return { status, json: () => Promise.resolve(body) } as unknown as Response;
}

describe("opening and leaving (REQ-037)", () => {
  it("opens seeded from field-setting defaults — the dirty baseline", () => {
    const { onLeave } = renderCreate();
    expect(screen.getByLabelText<HTMLInputElement>(/Note/).value).toBe("welcome");
    // Still at its defaults: closing asks nothing, creates nothing.
    fireEvent.keyDown(screen.getByLabelText(/Name/), { key: "Escape" });
    expect(onLeave).toHaveBeenCalledTimes(1);
  });

  it("guards authored input, and discard leaves without creating", () => {
    const { onLeave } = renderCreate();
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: "Ada" } });
    fireEvent.keyDown(screen.getByLabelText(/Name/), { key: "Escape" });
    expect(screen.getByText(/1 field has been changed but not saved/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Discard and leave" }));
    expect(onLeave).toHaveBeenCalledTimes(1);
  });
});

describe("the advisory similar-records offer (REQ-037)", () => {
  it("arms on identity-field exit and presents compare + continue — never a block", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      envelope(200, {
        data: {
          candidates: [
            {
              record: { mentorID: "m-9", mentorName: "Ada Lovelace", rowVersion: 2 },
              removed: false,
            },
          ],
          matchedRuleNames: ["byName"],
          blocking: false,
        },
        meta: {},
        errors: null,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { onCreated } = renderCreate();
    const name = screen.getByLabelText(/Name/);
    fireEvent.change(name, { target: { value: "Ada Lovelace" } });
    fireEvent.blur(name);
    const dialog = await screen.findByRole("dialog", { name: "Similar records exist" });
    // Declared non-blocking: no shell may render this as a wall.
    expect(dialog.getAttribute("data-blocking")).toBe("false");
    // Side-by-side: your value beside the candidate's.
    expect(screen.getAllByText("Ada Lovelace").length).toBeGreaterThanOrEqual(2);
    // Continue dismisses; nothing was created, Save stays the user's act.
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.queryByRole("dialog", { name: "Similar records exist" })).toBeNull();
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("a non-identity field never arms the check", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderCreate();
    const note = screen.getByLabelText(/Note/);
    fireEvent.change(note, { target: { value: "hello" } });
    fireEvent.blur(note);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("switch-to-existing opens that record and creates nothing", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      envelope(200, {
        data: {
          candidates: [
            {
              record: { mentorID: "m-9", mentorName: "Ada Lovelace", rowVersion: 2 },
              removed: false,
            },
          ],
          matchedRuleNames: ["byName"],
          blocking: false,
        },
        meta: {},
        errors: null,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { onCreated } = renderCreate();
    const name = screen.getByLabelText(/Name/);
    fireEvent.change(name, { target: { value: "Ada Lovelace" } });
    fireEvent.blur(name);
    await screen.findByRole("dialog", { name: "Similar records exist" });
    fireEvent.click(screen.getByRole("button", { name: "Use existing 1 instead" }));
    expect(onCreated).toHaveBeenCalledWith("m-9");
    // Only the advisory check traveled — no POST create.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("a removed match offers restore instead of create (REQ-037)", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        envelope(200, {
          data: {
            candidates: [
              {
                record: { mentorID: "m-9", mentorName: "Ada Lovelace", rowVersion: 4 },
                removed: true,
              },
            ],
            matchedRuleNames: ["byName"],
            blocking: false,
          },
          meta: {},
          errors: null,
        }),
      )
      .mockResolvedValueOnce(
        envelope(200, {
          data: { record: { mentorID: "m-9", rowVersion: 5, deletedAt: null } },
          meta: {},
          errors: null,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const { onCreated } = renderCreate();
    const name = screen.getByLabelText(/Name/);
    fireEvent.change(name, { target: { value: "Ada Lovelace" } });
    fireEvent.blur(name);
    await screen.findByRole("dialog", { name: "Similar records exist" });
    fireEvent.click(screen.getByRole("button", { name: "Restore existing 1 instead" }));
    await waitFor(() => {
      expect(onCreated).toHaveBeenCalledWith("m-9");
    });
    const [path, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    // The restore write, under the candidate's rowVersion (DB-S4).
    expect(path).toBe("/records/mentor/m-9/restore");
    expect(JSON.parse(init.body as string)).toEqual({ rowVersion: 4 });
  });
});

describe("saving (REQ-037/059)", () => {
  it("POSTs the authored values and lands on the new record's read view", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      envelope(200, {
        data: { record: { mentorID: "m-new", mentorName: "Ada", rowVersion: 1 } },
        meta: {},
        errors: null,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { onCreated } = renderCreate();
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: "Ada" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(onCreated).toHaveBeenCalledWith("m-new");
    });
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/records/mentor");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      values: { mentorName: "Ada", mentorNote: "welcome" },
    });
  });

  it("the save sweep still runs — an empty required field never travels", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderCreate();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText("This field is required.")).toBeTruthy();
  });

  it("the server's 409 re-presents the offer enforced; continue records the override", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        envelope(409, {
          data: [{ mentorID: "m-9", mentorName: "Ada", rowVersion: 2 }],
          meta: {},
          errors: [
            {
              fieldName: null,
              code: "duplicateCandidates",
              message: "Possible duplicate records exist.",
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        envelope(200, {
          data: { record: { mentorID: "m-new", mentorName: "Ada", rowVersion: 1 } },
          meta: {},
          errors: null,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const { onCreated } = renderCreate();
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: "Ada" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("dialog", { name: "Similar records exist" });
    fireEvent.change(screen.getByLabelText(/Why is this not a duplicate/), {
      target: { value: "different person" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue and create anyway" }));
    await waitFor(() => {
      expect(onCreated).toHaveBeenCalledWith("m-new");
    });
    const [, retryInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    // Continuing is allowed, and remembered (REQ-059).
    expect(JSON.parse(retryInit.body as string)).toEqual({
      values: { mentorName: "Ada", mentorNote: "welcome" },
      overrideDuplicates: true,
      overrideReason: "different person",
    });
  });
});
