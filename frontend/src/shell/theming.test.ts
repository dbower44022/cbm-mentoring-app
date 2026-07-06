/**
 * WTK-231 unit coverage: the slot/step → custom-property derivations are the
 * contract between the backend's camelCase vocabularies and the `--slot-*` /
 * `--type-step-*` properties shell.css declares — a drifted name would apply
 * a theme to variables nothing consumes.
 */
import { describe, expect, it } from "vitest";
import { slotCssVariable, typeStepVariable } from "./theming";

describe("slotCssVariable", () => {
  it("derives the shell.css names for the banding slots already in use", () => {
    expect(slotCssVariable("rowBackground")).toBe("--slot-row-background");
    expect(slotCssVariable("rowAlternateBackground")).toBe(
      "--slot-row-alternate-background",
    );
    expect(slotCssVariable("selectedRowBackground")).toBe(
      "--slot-selected-row-background",
    );
    expect(slotCssVariable("selectedRowText")).toBe("--slot-selected-row-text");
  });

  it("derives chrome and status slot names mechanically", () => {
    expect(slotCssVariable("appBackground")).toBe("--slot-app-background");
    expect(slotCssVariable("accent")).toBe("--slot-accent");
    expect(slotCssVariable("groupHeaderText")).toBe("--slot-group-header-text");
    expect(slotCssVariable("statusNegative")).toBe("--slot-status-negative");
  });
});

describe("typeStepVariable", () => {
  it("names steps with the backend's step keys", () => {
    expect(typeStepVariable("xs")).toBe("--type-step-xs");
    expect(typeStepVariable("md")).toBe("--type-step-md");
    expect(typeStepVariable("xl")).toBe("--type-step-xl");
  });
});
