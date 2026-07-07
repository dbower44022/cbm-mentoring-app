/**
 * Conditional-formatting evaluation (REQ-045, FND-909 D7): the pure walk
 * from a view's served rules to the effects one row paints. The server's
 * panel payload carries the rules in evaluation order; this module applies
 * them first-match-wins PER TARGET and answers CSS `var()` references onto
 * the active theme's status slots — a rule names a slot, never a literal
 * color (FND-906), so switching templates recolors every effect coherently.
 *
 * The backend vocabulary home is `mentorapp/storage/theming.py`
 * (CONDITION_OPERATORS / FORMATTING_EFFECTS / STATUS_COLOR_SLOTS); like
 * ./format.ts mirrors the column-format kinds, this file is that
 * vocabulary's one frontend mirror — never a third copy. CSS variable names
 * are DERIVED through shell/theming.ts's one slot→property derivation, so
 * no slot→variable table exists here either.
 */

import { slotCssVariable } from "../shell/theming";
import { type FormattingRulePayload } from "./payloads";

/**
 * The fixed status slots an effect may draw from — the frontend mirror of
 * storage.theming's STATUS_COLOR_SLOTS (REQ-045: exactly three, no rule
 * ever mints a slot).
 */
export const STATUS_SLOT_NAMES = [
  "statusPositive",
  "statusWarning",
  "statusNegative",
] as const;

/** A grid cell's wire value, as the rows payload carries it. */
type CellValue = string | number | null | undefined;

/**
 * What one row paints after the first-match-wins walk. Row-scoped effects
 * (`rowBackground`, `rowText`) have ONE target — the row — so each holds at
 * most one winner. The `accent` effect's target is the CONDITION FIELD's
 * cell (the reconciled reading of REQ-045's "small status icon": the small
 * visual renders where the judged value renders), so accent winners are
 * keyed per field — rules on different fields are different targets and may
 * all apply. Every value is a ready-to-assign `var(--slot-…)` reference.
 */
export interface RowEffects {
  rowBackground: string | null;
  rowText: string | null;
  accentByField: Record<string, string>;
}

function isAbsent(value: CellValue): boolean {
  return value === null || value === undefined || value === "";
}

function asNumbers(actual: string, expected: string): [number, number] | null {
  const a = Number(actual);
  const b = Number(expected);
  // Number("") is 0, but absence never reaches here (isAbsent gates first).
  return Number.isFinite(a) && Number.isFinite(b) ? [a, b] : null;
}

function matches(rule: FormattingRulePayload, value: CellValue): boolean {
  switch (rule.conditionOperator) {
    case "isEmpty":
      return isAbsent(value);
    case "isNotEmpty":
      return !isAbsent(value);
    default:
      break;
  }
  // Comparison operators judge a PRESENT value: absence is the presence
  // operators' domain, so a rule about a value never fires on a blank cell
  // (a "notEquals Active" rule must not light up empty rows).
  if (isAbsent(value) || rule.conditionValue === null) {
    return false;
  }
  // The wire carries JSON scalars; comparing their string forms keeps 3 and
  // "3" one value, the same stance the panel surface's search/sort take.
  const actual = String(value);
  const expected = String(rule.conditionValue);
  switch (rule.conditionOperator) {
    case "equals":
      return actual === expected;
    case "notEquals":
      return actual !== expected;
    case "contains":
      // Case-insensitive, matching the grid's REQ-020 search stance — a
      // rule author writing "hold" means On Hold, not a casing puzzle.
      return actual.toLowerCase().includes(expected.toLowerCase());
    case "greaterThan":
    case "lessThan": {
      const pair = asNumbers(actual, expected);
      const [a, b] = pair ?? [0, 0];
      const holds =
        pair !== null
          ? rule.conditionOperator === "greaterThan"
            ? a > b
            : a < b
          : rule.conditionOperator === "greaterThan"
            ? actual > expected
            : actual < expected;
      return holds;
    }
    default:
      // An operator this mirror doesn't know applies NO effect — a newer
      // server vocabulary must degrade to plain cells, never to a throw.
      return false;
  }
}

/**
 * Walk one row's values through the view's rules, first match wins per
 * target. A rule whose `effectSlot` is not one of the fixed status slots
 * applies no effect at all (and does not consume its target — a later
 * well-formed rule may still win): painting from outside the slot structure
 * is exactly what REQ-045 forbids.
 */
export function rowEffects(
  rules: readonly FormattingRulePayload[],
  values: Readonly<Record<string, string | number | null>>,
): RowEffects {
  const effects: RowEffects = { rowBackground: null, rowText: null, accentByField: {} };
  for (const rule of rules) {
    if (!(STATUS_SLOT_NAMES as readonly string[]).includes(rule.effectSlot)) {
      continue;
    }
    if (!matches(rule, values[rule.conditionField])) {
      continue;
    }
    // The one derivation (shell/theming.ts): statusWarning →
    // var(--slot-status-warning) — no second slot→variable mapping.
    const reference = `var(${slotCssVariable(rule.effectSlot)})`;
    if (rule.effect === "rowBackground") {
      effects.rowBackground ??= reference;
    } else if (rule.effect === "rowText") {
      effects.rowText ??= reference;
    } else if (
      rule.effect === "accent" &&
      !(rule.conditionField in effects.accentByField)
    ) {
      effects.accentByField[rule.conditionField] = reference;
    }
    // An unknown effect kind falls through unapplied — same degrade-to-plain
    // stance as an unknown operator.
  }
  return effects;
}
