/**
 * The conditional-formatting evaluator (REQ-045, FND-909 D7): standard
 * operators over row values, first-match-wins per target, slot-derived CSS
 * variable references, and the degrade-to-plain stances (unknown slot,
 * unknown operator, absent values under comparison operators).
 */

import { describe, expect, it } from "vitest";

import { rowEffects } from "./formatting";
import { type FormattingRulePayload } from "./payloads";

function rule(overrides: Partial<FormattingRulePayload>): FormattingRulePayload {
  return {
    conditionField: "engagementStatusLabel",
    conditionOperator: "equals",
    conditionValue: "Active",
    effect: "accent",
    effectSlot: "statusPositive",
    ...overrides,
  };
}

describe("operators", () => {
  it("equals matches the exact value and answers the derived slot var", () => {
    const effects = rowEffects([rule({})], { engagementStatusLabel: "Active" });
    // The reference derives through shell/theming's ONE slot→property
    // derivation — statusPositive → --slot-status-positive, never a
    // hand-kept mapping here.
    expect(effects.accentByField).toEqual({
      engagementStatusLabel: "var(--slot-status-positive)",
    });
    expect(
      rowEffects([rule({})], { engagementStatusLabel: "Dormant" }).accentByField,
    ).toEqual({});
  });

  it("equals speaks string forms across wire types, and never fires on absence", () => {
    const numeric = rule({ conditionField: "totalSessions", conditionValue: 3 });
    expect(rowEffects([numeric], { totalSessions: 3 }).accentByField).not.toEqual({});
    expect(rowEffects([numeric], { totalSessions: "3" }).accentByField).not.toEqual({});
    // Absent values are the presence operators' domain.
    expect(rowEffects([numeric], { totalSessions: null }).accentByField).toEqual({});
  });

  it("notEquals matches a differing present value only", () => {
    const differs = rule({ conditionOperator: "notEquals", conditionValue: "Active" });
    expect(
      rowEffects([differs], { engagementStatusLabel: "On Hold" }).accentByField,
    ).not.toEqual({});
    expect(
      rowEffects([differs], { engagementStatusLabel: "Active" }).accentByField,
    ).toEqual({});
    // A blank cell is not "not equal to Active" — comparison operators
    // never judge absence.
    expect(rowEffects([differs], { engagementStatusLabel: "" }).accentByField).toEqual(
      {},
    );
  });

  it("contains is a case-insensitive substring, the grid search stance", () => {
    const holds = rule({ conditionOperator: "contains", conditionValue: "hold" });
    expect(
      rowEffects([holds], { engagementStatusLabel: "On Hold" }).accentByField,
    ).not.toEqual({});
    expect(
      rowEffects([holds], { engagementStatusLabel: "Active" }).accentByField,
    ).toEqual({});
  });

  it("isEmpty/isNotEmpty judge presence, value-free", () => {
    const empty = rule({ conditionOperator: "isEmpty", conditionValue: null });
    expect(
      rowEffects([empty], { engagementStatusLabel: null }).accentByField,
    ).not.toEqual({});
    expect(
      rowEffects([empty], { engagementStatusLabel: "" }).accentByField,
    ).not.toEqual({});
    expect(rowEffects([empty], {}).accentByField).not.toEqual({});
    expect(
      rowEffects([empty], { engagementStatusLabel: "Active" }).accentByField,
    ).toEqual({});
    const present = rule({ conditionOperator: "isNotEmpty", conditionValue: null });
    expect(
      rowEffects([present], { engagementStatusLabel: "Active" }).accentByField,
    ).not.toEqual({});
    expect(
      rowEffects([present], { engagementStatusLabel: null }).accentByField,
    ).toEqual({});
  });

  it("an operator this mirror does not know applies no effect, never a throw", () => {
    const alien = rule({ conditionOperator: "matchesRegex", conditionValue: ".*" });
    expect(
      rowEffects([alien], { engagementStatusLabel: "Active" }).accentByField,
    ).toEqual({});
  });
});

describe("first match wins per target", () => {
  it("the first matching rule for a target wins; later matches never stack", () => {
    const effects = rowEffects(
      [
        rule({
          conditionOperator: "isNotEmpty",
          conditionValue: null,
          effectSlot: "statusWarning",
        }),
        rule({ effectSlot: "statusNegative" }),
      ],
      { engagementStatusLabel: "Active" },
    );
    expect(effects.accentByField.engagementStatusLabel).toBe(
      "var(--slot-status-warning)",
    );
  });

  it("row effects and per-field accents are separate targets", () => {
    const effects = rowEffects(
      [
        rule({ effect: "rowBackground", effectSlot: "statusWarning" }),
        rule({ effect: "rowText", effectSlot: "statusNegative" }),
        rule({}),
        // A second field is a second accent target: both cells paint.
        rule({
          conditionField: "openActionItems",
          conditionOperator: "greaterThan",
          conditionValue: 0,
          effectSlot: "statusWarning",
        }),
      ],
      { engagementStatusLabel: "Active", openActionItems: 2 },
    );
    expect(effects.rowBackground).toBe("var(--slot-status-warning)");
    expect(effects.rowText).toBe("var(--slot-status-negative)");
    expect(effects.accentByField).toEqual({
      engagementStatusLabel: "var(--slot-status-positive)",
      openActionItems: "var(--slot-status-warning)",
    });
  });
});

describe("the fixed slot structure (REQ-045)", () => {
  it("an unknown effectSlot applies no effect and does not consume the target", () => {
    const effects = rowEffects(
      [
        // Neither an unknown name nor a literal color may paint (FND-906).
        rule({ effectSlot: "statusUrgent" }),
        rule({ effectSlot: "#ff0000" }),
        rule({ effectSlot: "statusWarning" }),
      ],
      { engagementStatusLabel: "Active" },
    );
    expect(effects.accentByField.engagementStatusLabel).toBe(
      "var(--slot-status-warning)",
    );
  });

  it("no matching rule means a plain row: null row effects, no accents", () => {
    const effects = rowEffects([rule({})], { engagementStatusLabel: "Dormant" });
    expect(effects).toEqual({ rowBackground: null, rowText: null, accentByField: {} });
  });
});
