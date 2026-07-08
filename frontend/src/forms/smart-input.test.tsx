/**
 * @vitest-environment jsdom
 *
 * Smart input on the form engine (REQ-034, REL-004 block 1): typed fields
 * auto-format on focus exit through the server's one formatter; a composite
 * paste resolves into components with the remainder kept visible and the
 * paste never blocked; a postal code landing fills EMPTY city/state only.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type EditFormPayload, type FormFieldPayload } from "../api/payloads";
import { EditFormScreen } from "./edit-form";

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

function payload(): EditFormPayload {
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
      fullName: null,
      firstName: null,
      lastName: null,
      mentorPhone: null,
      postalCode: null,
      cityName: null,
      stateCode: null,
      rowVersion: 1,
    },
    fields: [
      field("fullName", { fieldType: "personName" }),
      field("firstName"),
      field("lastName"),
      field("mentorPhone", { fieldType: "phone" }),
      field("postalCode", { fieldType: "postalCode" }),
      field("cityName"),
      field("stateCode"),
    ],
    initialFocusField: "fullName",
  };
}

function renderForm(): void {
  render(
    <EditFormScreen
      entityType="mentor"
      recordId="m-1"
      payload={payload()}
      onLeave={vi.fn()}
    />,
  );
}

function ok(data: unknown): Response {
  return {
    status: 200,
    json: () => Promise.resolve({ data, meta: {}, errors: null }),
  } as unknown as Response;
}

function input(name: string): HTMLInputElement {
  return screen.getByLabelText<HTMLInputElement>(name, { exact: true });
}

describe("smart input (REQ-034)", () => {
  it("auto-formats a typed field on focus exit through the one formatter", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ value: "(216) 555-1234" }));
    vi.stubGlobal("fetch", fetchMock);
    renderForm();
    const phone = input("mentorPhone");
    fireEvent.change(phone, { target: { value: "2165551234" } });
    fireEvent.blur(phone);
    await waitFor(() => {
      expect(input("mentorPhone").value).toBe("(216) 555-1234");
    });
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/form-input/format");
    expect(JSON.parse(init.body as string)).toEqual({
      fieldType: "phone",
      value: "2165551234",
    });
  });

  it("resolves a composite paste: components fill, remainder stays visible", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        ok({ components: { firstName: "Ada", lastName: "Lovelace" }, remainder: "" }),
      );
    vi.stubGlobal("fetch", fetchMock);
    renderForm();
    const fullName = input("fullName");
    fireEvent.paste(fullName, {
      clipboardData: { getData: () => "Lovelace, Ada" },
    });
    await waitFor(() => {
      expect(input("firstName").value).toBe("Ada");
    });
    expect(input("lastName").value).toBe("Lovelace");
    // Fully resolved: nothing remains in the pasted-into control.
    expect(input("fullName").value).toBe("");
  });

  it("an unconfident paste keeps the whole text visible — never discarded", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(ok({ components: {}, remainder: "some ambiguous words" }));
    vi.stubGlobal("fetch", fetchMock);
    renderForm();
    const fullName = input("fullName");
    fireEvent.paste(fullName, {
      clipboardData: { getData: () => "some ambiguous words" },
    });
    await waitFor(() => {
      expect(input("fullName").value).toBe("some ambiguous words");
    });
  });

  it("a postal code landing fills EMPTY city/state only", async () => {
    const fetchMock = vi.fn().mockImplementation((path: string) => {
      if (path.startsWith("/form-input/format")) {
        return Promise.resolve(ok({ value: "44113" }));
      }
      return Promise.resolve(ok({ fill: { cityName: "Cleveland", stateCode: "OH" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderForm();
    // The user already typed a city: the fill must NOT overwrite it.
    fireEvent.change(input("cityName"), { target: { value: "Lakewood" } });
    const postal = input("postalCode");
    fireEvent.change(postal, { target: { value: "44113" } });
    fireEvent.blur(postal);
    await waitFor(() => {
      expect(input("stateCode").value).toBe("OH");
    });
    expect(input("cityName").value).toBe("Lakewood");
  });
});
