/**
 * @vitest-environment jsdom
 *
 * The full-screen edit form (REQ-032/033/038/039/040, REL-004 block 1):
 * value lifecycle (dirty by value comparison, Cancel reverts and stays
 * open, Save PATCHes only the changed fields against rowVersion, a landed
 * save rebases), field-settings-driven validation (asterisk, on-exit check,
 * the save sweep focusing the first problem, server errors placed inline),
 * the REQ-038 keyboard (Ctrl+S, Enter never submits, restricted Tab), the
 * REQ-039 read-only click-to-explain, and the REQ-040 help marker.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type EditFormPayload, type FormFieldPayload } from "../api/payloads";
import {
  blankToNull,
  dirtyChanges,
  EditFormScreen,
  leaveWarning,
  resolveStaleSave,
  validateOnExit,
} from "./edit-form";

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
    visibilityHints: null,
    optionSet: null,
    editable: true,
    readOnly: null,
    help: null,
    ...overrides,
  };
}

function systemField(name: string): FormFieldPayload {
  return field(name, {
    fieldType: "system",
    editable: false,
    readOnly: {
      kind: "system",
      explanation: {
        whatHappened: `'${name}' can't be edited.`,
        why: "This is a system field; it is maintained automatically.",
        whatNext: "No action is needed — the system keeps it up to date.",
      },
      rendering: {
        position: "inPlace",
        value: "readValue",
        click: "explain",
        tabStop: false,
      },
    },
  });
}

function payload(overrides: Partial<EditFormPayload> = {}): EditFormPayload {
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
    record: {
      mentorID: "m-1",
      mentorName: "Ada Lovelace",
      mentorNote: "old",
      rowVersion: 3,
    },
    fields: [
      field("mentorName", { fieldLabel: "Name", requiredFlag: true }),
      field("mentorNote", { fieldLabel: "Note" }),
      systemField("createdAt"),
    ],
    initialFocusField: "mentorName",
    ...overrides,
  };
}

function renderForm(p: EditFormPayload = payload(), onLeave = vi.fn()): typeof onLeave {
  render(
    <EditFormScreen entityType="mentor" recordId="m-1" payload={p} onLeave={onLeave} />,
  );
  return onLeave;
}

function envelope(status: number, body: unknown): Response {
  return {
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

describe("the pure helpers mirror the Python contracts", () => {
  it("blank text is NO value (form_validation.normalized_input)", () => {
    expect(blankToNull("   ")).toBeNull();
    expect(blankToNull("x")).toBe("x");
    expect(blankToNull(0)).toBe(0);
  });

  it("dirty is a value comparison — retyping the original is clean again", () => {
    const original = { a: "1", b: "2" };
    expect(dirtyChanges(original, { a: "1", b: "2" })).toEqual({});
    expect(dirtyChanges(original, { a: "9", b: "2" })).toEqual({ a: "9" });
  });

  it("on-exit validation speaks the server's words (REQ-033)", () => {
    expect(validateOnExit(field("f", { requiredFlag: true }), "  ")).toBe(
      "This field is required.",
    );
    expect(validateOnExit(field("f"), "")).toBeNull();
    const choice = field("f", {
      fieldType: "choice",
      optionSet: {
        optionSetID: "s",
        optionSetName: "s",
        optionValues: [
          {
            optionValueID: "v1",
            optionValueName: "one",
            optionValueLabel: "One",
            optionValueSortOrder: 1,
            activeFlag: false,
          },
        ],
      },
    });
    expect(validateOnExit(choice, "v1")).toBe("This value has been retired.");
    expect(validateOnExit(choice, "nope")).toBe("Not a value in this list.");
  });

  it("the leave warning names every changed field (edit_form.leave_warning)", () => {
    expect(leaveWarning(["Name"]).why).toBe(
      "1 field has been changed but not saved: Name.",
    );
  });

  it("a stale save resolves exactly as edit_safety does", () => {
    const loaded = { a: "1", b: "2", rowVersion: 3 };
    // The other save touched a DIFFERENT field: invisible auto-retry.
    const retry = resolveStaleSave(
      { a: "9" },
      loaded,
      { a: "1", b: "X", rowVersion: 4 },
      1,
    );
    expect(retry).toEqual({ kind: "retry", changes: { a: "9" }, rowVersion: 4 });
    // The other save already wrote the same value: nothing left to send.
    const current = resolveStaleSave(
      { a: "9" },
      loaded,
      { a: "9", b: "2", rowVersion: 4 },
      1,
    );
    expect(current).toEqual({ kind: "alreadyCurrent", rowVersion: 4 });
    // A true overlap: the walk-through, never a silent overwrite.
    const merge = resolveStaleSave(
      { a: "9" },
      loaded,
      { a: "7", b: "2", rowVersion: 4 },
      1,
    );
    expect(merge.kind).toBe("manualMerge");
  });
});

describe("the form frame (REQ-032/033/039/040)", () => {
  it("renders the large Save, required asterisk, and read-only field in place", () => {
    renderForm();
    const save = screen.getByRole("button", { name: "Save" });
    expect(save.dataset.prominence).toBe("large");
    // Required-ness comes from field settings, marked on the label.
    expect(screen.getByText("Name").parentElement?.textContent).toContain("*");
    // The system field renders its read value — not an input, no tab stop.
    const readonly = screen.getByRole("button", { name: /—|20/ });
    expect(readonly.tabIndex).toBe(-1);
  });

  it("clicking a read-only field explains why it is not editable (REQ-039)", () => {
    renderForm();
    const readonly = document.querySelector('[data-readonly-kind="system"]');
    if (readonly === null) {
      throw new Error("the system field's read value did not render");
    }
    fireEvent.click(readonly);
    expect(
      screen.getByText("This is a system field; it is maintained automatically."),
    ).toBeTruthy();
  });

  it("shows the help marker only where field settings carry text (REQ-040)", () => {
    const withHelp = payload();
    withHelp.fields[1] = field("mentorNote", {
      fieldLabel: "Note",
      help: {
        helpText: "Anything worth remembering.",
        rendering: {
          marker: "infoMarker",
          placement: "fieldLabel",
          reveal: ["hover", "focus"],
          persistent: false,
          tabStop: false,
        },
      },
    });
    renderForm(withHelp);
    expect(screen.getByText("Anything worth remembering.")).toBeTruthy();
    expect(screen.getByLabelText("About Note")).toBeTruthy();
    // No marker anywhere else — an empty marker would be worse than none.
    expect(screen.queryByLabelText("About Name")).toBeNull();
  });

  it("Cancel reverts to the originals and stays open (REQ-032)", () => {
    renderForm();
    const name = screen.getByLabelText<HTMLInputElement>(/Name/);
    fireEvent.change(name, { target: { value: "Grace Hopper" } });
    expect(name.value).toBe("Grace Hopper");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByLabelText<HTMLInputElement>(/Name/).value).toBe("Ada Lovelace");
  });

  it("validates per field on exit, inline at the field (REQ-033)", () => {
    renderForm();
    const name = screen.getByLabelText(/Name/);
    fireEvent.change(name, { target: { value: "   " } });
    fireEvent.blur(name);
    expect(screen.getByText("This field is required.")).toBeTruthy();
    fireEvent.change(name, { target: { value: "Grace" } });
    fireEvent.blur(name);
    expect(screen.queryByText("This field is required.")).toBeNull();
  });
});

describe("saving (REQ-032/033, DB-S12)", () => {
  it("PATCHes only the changed fields with rowVersion, then rebases", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      envelope(200, {
        data: {
          record: {
            mentorID: "m-1",
            mentorName: "Grace",
            mentorNote: "old",
            rowVersion: 4,
          },
        },
        meta: {},
        errors: null,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderForm();
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: "Grace" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/records/mentor/m-1");
    expect(init.method).toBe("PATCH");
    // Only the changed field travels, plus the concurrency base.
    expect(JSON.parse(init.body as string)).toEqual({
      mentorName: "Grace",
      rowVersion: 3,
    });
    await screen.findByText(/Saved at/);
  });

  it("a clean Save is a declared no-op — no PATCH travels", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderForm();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("the save sweep reports the problem and focuses the first offender", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderForm();
    const name = screen.getByLabelText(/Name/);
    fireEvent.change(name, { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText("This field is required.")).toBeTruthy();
    expect(document.activeElement).toBe(name);
  });

  it("places a 422's server errors inline at their fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      envelope(422, {
        data: null,
        meta: {},
        errors: [
          { fieldName: "mentorNote", code: "typeMismatch", message: "Expected text." },
          { fieldName: null, code: "somethingElse", message: "A form-level problem." },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderForm();
    fireEvent.change(screen.getByLabelText(/Note/), { target: { value: "new" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Expected text.")).toBeTruthy();
    // Nothing is dropped: the entry no displayed field owns lands form-level.
    expect(screen.getByText("A form-level problem.")).toBeTruthy();
  });

  it("auto-retries a stale save the other side didn't overlap (REQ-013)", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        envelope(409, {
          data: {
            mentorID: "m-1",
            mentorName: "Ada Lovelace",
            mentorNote: "theirs",
            rowVersion: 5,
          },
          meta: {},
          errors: [
            { fieldName: null, code: "staleRowVersion", message: "changed since read" },
          ],
        }),
      )
      .mockResolvedValueOnce(
        envelope(200, {
          data: {
            record: {
              mentorID: "m-1",
              mentorName: "Grace",
              mentorNote: "theirs",
              rowVersion: 6,
            },
          },
          meta: {},
          errors: null,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    renderForm();
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: "Grace" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
    const [, retryInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    // The invisible retry: same change, the FRESH version.
    expect(JSON.parse(retryInit.body as string)).toEqual({
      mentorName: "Grace",
      rowVersion: 5,
    });
    await screen.findByText(/Saved at/);
  });

  it("a true overlap opens the walk-through, never a silent overwrite", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      envelope(409, {
        data: {
          mentorID: "m-1",
          mentorName: "Their Name",
          mentorNote: "old",
          rowVersion: 5,
        },
        meta: {},
        errors: [
          { fieldName: null, code: "staleRowVersion", message: "changed since read" },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderForm();
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: "My Name" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(
      await screen.findByText(/Someone else saved this record while you were editing./),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: /Keep mine: My Name/ })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /Take theirs: Their Name/ }),
    ).toBeTruthy();
  });
});

describe("the keyboard (REQ-038)", () => {
  it("Ctrl+S saves", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      envelope(200, {
        data: {
          record: {
            mentorID: "m-1",
            mentorName: "Grace",
            mentorNote: "old",
            rowVersion: 4,
          },
        },
        meta: {},
        errors: null,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderForm();
    const name = screen.getByLabelText(/Name/);
    fireEvent.change(name, { target: { value: "Grace" } });
    fireEvent.keyDown(name, { key: "s", ctrlKey: true });
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
  });

  it("Escape requests leave through the dirty guard — and discard proceeds", () => {
    const onLeave = renderForm();
    const name = screen.getByLabelText(/Name/);
    fireEvent.change(name, { target: { value: "Grace" } });
    fireEvent.keyDown(name, { key: "Escape" });
    // The guard names the changed field (edit_form.leave_warning).
    expect(
      screen.getByText(/1 field has been changed but not saved: Name\./),
    ).toBeTruthy();
    expect(onLeave).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(onLeave).toHaveBeenCalledTimes(1);
  });

  it("Escape on a clean form leaves with no ceremony", () => {
    const onLeave = renderForm();
    fireEvent.keyDown(screen.getByLabelText(/Name/), { key: "Escape" });
    expect(onLeave).toHaveBeenCalledTimes(1);
  });

  it("Tab cycles editable fields only and wraps (form_keyboard.next_tab_stop)", () => {
    renderForm();
    const name = screen.getByLabelText(/Name/);
    const note = screen.getByLabelText(/Note/);
    name.focus();
    fireEvent.keyDown(name, { key: "Tab" });
    expect(document.activeElement).toBe(note);
    // The wrap: past the last stop, back to the first — never Save/read-only.
    fireEvent.keyDown(note, { key: "Tab" });
    expect(document.activeElement).toBe(name);
    // Save and Cancel are not tab stops (Ctrl+S is the keyboard's Save path).
    expect(screen.getByRole("button", { name: "Save" }).tabIndex).toBe(-1);
    expect(screen.getByRole("button", { name: "Cancel" }).tabIndex).toBe(-1);
  });

  it("Enter never submits the form", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderForm();
    const name = screen.getByLabelText(/Name/);
    fireEvent.change(name, { target: { value: "Grace" } });
    fireEvent.keyDown(name, { key: "Enter" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
