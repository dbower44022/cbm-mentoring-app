/**
 * @vitest-environment jsdom
 *
 * The Themes surface (REQ-044/046): the picker chooses a template (records
 * the layer-two preference and repaints), the creator fills the fixed slots
 * and saves, and the contrast guardrail warns with a preview but NEVER
 * blocks the save.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Appearance } from "./appearance";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function envelope(status: number, body: unknown): Response {
  return { status, json: () => Promise.resolve(body) } as unknown as Response;
}

function templateList(): unknown {
  return {
    data: [
      {
        colorTemplateID: "sys-standard",
        colorTemplateName: "Standard",
        templateType: "system",
        launchSetKey: "standard",
        colorSlots: {
          panelBackground: "#ffffff",
          accent: "#2a6f97",
          headerBackground: "#1d3557",
          rowAlternateBackground: "#f0f4f8",
        },
        rowVersion: 1,
      },
      {
        colorTemplateID: "sys-dark",
        colorTemplateName: "Dark",
        templateType: "system",
        launchSetKey: "dark",
        colorSlots: {
          panelBackground: "#1a1a1a",
          accent: "#7fb2d9",
          headerBackground: "#000000",
          rowAlternateBackground: "#222222",
        },
        rowVersion: 1,
      },
    ],
    meta: {},
    errors: null,
  };
}

describe("the themes picker (REQ-044)", () => {
  it("lists the launch set and applies a choice via the layer-two preference", async () => {
    const calls: { url: string; init: RequestInit | undefined }[] = [];
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/theming/templates") {
        return Promise.resolve(envelope(200, templateList()));
      }
      if (url.startsWith("/preferences/")) {
        return Promise.resolve(envelope(200, { data: {}, meta: {}, errors: null }));
      }
      // applyEffectiveTheme's /theming/effective re-fetch after the choice.
      return Promise.resolve(
        envelope(200, {
          data: { colorSlots: {}, fontSlots: {}, typeScale: { scaleSteps: {} } },
          meta: {},
          errors: null,
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Appearance onClose={vi.fn()} />);
    await screen.findByRole("button", { name: /Standard/ });
    expect(screen.getByRole("button", { name: /Dark/ })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Dark/ }));

    await waitFor(() => {
      const pref = calls.find((c) => c.url.startsWith("/preferences/"));
      expect(pref).toBeDefined();
    });
    const pref = calls.find((c) => c.url.startsWith("/preferences/"));
    expect(pref?.url).toContain("theming.templateChoice");
    // A system template is chosen by its launch-set key (the resolver form).
    expect(JSON.parse(pref?.init?.body as string)).toEqual({
      preferenceValue: { templateKey: "dark" },
    });
    // The choice repainted via a re-fetch of the effective theme.
    expect(calls.some((c) => c.url === "/theming/effective")).toBe(true);
  });
});

describe("the template creator + contrast guardrail (REQ-044/046)", () => {
  it("saves a filled template and shows contrast warnings WITHOUT blocking", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/theming/templates" && (init?.method ?? "GET") === "GET") {
        return Promise.resolve(envelope(200, templateList()));
      }
      if (url === "/theming/templates" && init?.method === "POST") {
        // The save SUCCEEDED; the guardrail rides meta, never errors.
        return Promise.resolve(
          envelope(200, {
            data: {
              colorTemplateID: "user-1",
              colorTemplateName: "Mine",
              templateType: "user",
              launchSetKey: null,
              colorSlots: {},
              rowVersion: 1,
            },
            meta: {
              contrastWarnings: [
                {
                  kind: "readability",
                  ratioLabel: "2.10:1 — below 4.5:1",
                  preview: {
                    textColor: "#888888",
                    backgroundColor: "#ffffff",
                    sampleText: "Sample",
                  },
                  message: {
                    whatHappened: "This text may be hard to read.",
                    why: "The contrast ratio is below the readable minimum.",
                    whatNext: "Darken the text or lighten the background.",
                  },
                },
              ],
            },
            errors: null,
          }),
        );
      }
      return Promise.resolve(envelope(200, { data: {}, meta: {}, errors: null }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const onClose = vi.fn();
    render(<Appearance onClose={onClose} />);
    fireEvent.click(await screen.findByRole("button", { name: "New template…" }));

    fireEvent.change(screen.getByLabelText("Template name"), {
      target: { value: "Mine" },
    });
    // The base size step is a defined step, never a raw size (REQ-046).
    expect(screen.getByLabelText("Base size step")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Save template" }));

    // The guardrail warning renders with its live preview — and the save
    // already succeeded (never blocked): the "Keep and close" path is offered.
    expect(await screen.findByText("This text may be hard to read.")).toBeTruthy();
    expect(screen.getByText("2.10:1 — below 4.5:1")).toBeTruthy();
    const posted = fetchMock.mock.calls.find(
      (c) =>
        c[0] === "/theming/templates" &&
        (c[1] as RequestInit | undefined)?.method === "POST",
    );
    expect(posted).toBeDefined();
    const body = JSON.parse((posted?.[1] as RequestInit).body as string) as {
      colorTemplateName: string;
      typeStepChoice: string;
      colorSlots: Record<string, string>;
    };
    expect(body.colorTemplateName).toBe("Mine");
    expect(body.typeStepChoice).toBe("md");
    // 15 fixed color slots travel in the document.
    expect(Object.keys(body.colorSlots)).toHaveLength(15);

    // "Keep and close" returns to the picker (the saved template now lives
    // there), not out of the whole surface — the New template… button is back.
    fireEvent.click(screen.getByRole("button", { name: "Keep and close" }));
    expect(await screen.findByRole("button", { name: "New template…" })).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("surfaces a structural refusal (off-scale / duplicate) as an error", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/theming/templates" && (init?.method ?? "GET") === "GET") {
        return Promise.resolve(envelope(200, templateList()));
      }
      if (url === "/theming/templates" && init?.method === "POST") {
        return Promise.resolve(
          envelope(422, {
            data: null,
            meta: {},
            errors: [
              {
                fieldName: "colorTemplateName",
                code: "duplicateTemplateName",
                message: "you already have a live template named 'Mine'.",
              },
            ],
          }),
        );
      }
      return Promise.resolve(envelope(200, { data: {}, meta: {}, errors: null }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Appearance onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "New template…" }));
    fireEvent.change(screen.getByLabelText("Template name"), {
      target: { value: "Mine" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save template" }));
    expect(
      await screen.findByText("you already have a live template named 'Mine'."),
    ).toBeTruthy();
    // The form stays open (the slot fields remain) — a refusal is not a close.
    expect(screen.getByLabelText("Base size step")).toBeTruthy();
  });
});
