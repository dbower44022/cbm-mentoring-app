/**
 * @vitest-environment jsdom
 *
 * The per-field edit window (REQ-035): self-contained small editor — own
 * Save/Cancel, the single-field PATCH under rowVersion, Save-at-base is a
 * declared no-op, Esc IS Cancel, and the standard stale-save resolution
 * (auto-retry / already-current / keep-mine-take-theirs).
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type FormFieldPayload } from "../api/payloads";
import { FieldEditWindow } from "./field-edit-window";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const FIELD: FormFieldPayload = {
  fieldName: "mentorName",
  fieldLabel: "Name",
  fieldType: "text",
  requiredFlag: true,
  validationRules: null,
  defaultValue: null,
  helpText: null,
  visibilityHints: null,
  optionSet: null,
  editable: true,
  readOnly: null,
  help: null,
};

function renderWindow(): {
  onSaved: ReturnType<typeof vi.fn>;
  onClose: ReturnType<typeof vi.fn>;
} {
  const onSaved = vi.fn();
  const onClose = vi.fn();
  render(
    <FieldEditWindow
      entityType="mentor"
      recordId="m-1"
      field={FIELD}
      baseValue="Ada Lovelace"
      rowVersion={3}
      onSaved={onSaved}
      onClose={onClose}
    />,
  );
  return { onSaved, onClose };
}

function envelope(status: number, body: unknown): Response {
  return { status, json: () => Promise.resolve(body) } as unknown as Response;
}

describe("the per-field window (REQ-035)", () => {
  it("declares itself: small window, commits a single-field PATCH", () => {
    renderWindow();
    const dialog = screen.getByRole("dialog", { name: "Edit Name" });
    expect(dialog.dataset.kind).toBe("smallWindow");
    expect(dialog.dataset.commits).toBe("singleFieldPatch");
  });

  it("Save commits exactly one field plus rowVersion, then reports the record", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      envelope(200, {
        data: { record: { mentorID: "m-1", mentorName: "Grace", rowVersion: 4 } },
        meta: {},
        errors: null,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { onSaved } = renderWindow();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Grace" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(onSaved).toHaveBeenCalledTimes(1);
    });
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/records/mentor/m-1");
    expect(init.method).toBe("PATCH");
    // single_field_patch: one field, the concurrency base, nothing else.
    expect(JSON.parse(init.body as string)).toEqual({
      mentorName: "Grace",
      rowVersion: 3,
    });
  });

  it("Save with the value back at base closes without a write (DB-S12)", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { onClose, onSaved } = renderWindow();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("Esc IS Cancel: discard directly, no guard", () => {
    const { onClose } = renderWindow();
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Grace" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("validates the one field before the write (REQ-033, one engine)", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderWindow();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText("This field is required.")).toBeTruthy();
  });

  it("auto-retries a stale save whose conflict is elsewhere on the record", async () => {
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
          data: { record: { mentorID: "m-1", mentorName: "Grace", rowVersion: 6 } },
          meta: {},
          errors: null,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const { onSaved } = renderWindow();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Grace" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(onSaved).toHaveBeenCalledTimes(1);
    });
    const [, retryInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(retryInit.body as string)).toEqual({
      mentorName: "Grace",
      rowVersion: 5,
    });
  });

  it("a true same-field overlap offers keep-mine / take-theirs — never silent", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      envelope(409, {
        data: { mentorID: "m-1", mentorName: "Their Name", rowVersion: 5 },
        meta: {},
        errors: [
          { fieldName: null, code: "staleRowVersion", message: "changed since read" },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { onClose } = renderWindow();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "My Name" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/was changed by someone else/);
    expect(screen.getByRole("button", { name: "Keep mine" })).toBeTruthy();
    // Take theirs: close without writing — their value already stands.
    fireEvent.click(screen.getByRole("button", { name: "Take theirs" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
