/**
 * Wire shapes for the grid panel (WTK-048), following the api/payloads.ts
 * precedent: hand-maintained mirrors of the server view-models until the
 * generated schema covers them. The shapes address the WTK-042 endpoint
 * contracts (`GET /grids/{gridId}/rows`, `GET /grids/{gridId}/aggregates`)
 * and the WTK-043 panel design (`ui/grid_panel.py`).
 */

import { type ColumnFormat } from "./format";

/** One action as record_preview.PanelAction serializes it. */
export interface ActionPayload {
  key: string;
  label: string;
  selectionContract: "none" | "single" | "multiple";
  classification: "safe" | "modifying" | "destructive";
}

export interface GridColumnPayload {
  fieldName: string;
  label: string;
  /**
   * The column's declared format kind (SKL-112: format is a view property;
   * FND-909 D1). Cells render through the one formatter (./format.ts)
   * keyed by this — the wire value stays raw, the LOOK is declared here.
   */
  format: ColumnFormat;
}

export interface GridViewPayload {
  viewKey: string;
  label: string;
  /** What the view shows, in words — cited by the empty-view state. */
  criteria: string;
  /**
   * The data source this view reads (REQ-018: the first thing a view
   * defines is its data source; `gridView.dataSourceID` in storage). The
   * workprocess action list is served per data source (REQ-041), so the
   * panel keys its `/workprocesses/actions/{key}` read off the ACTIVE view.
   */
  dataSourceKey: string;
  isSystemView: boolean;
  allowAdHocFilters: boolean;
}

/**
 * One conditional-formatting rule as the panel payload serves it (REQ-045,
 * FND-909 D7): a condition on a data-source field with a standard operator,
 * an effect from the fixed effect vocabulary, and an `effectSlot` naming a
 * status color slot of the active theme — never a literal color (FND-906).
 * The array order IS the first-match-wins evaluation order.
 */
export interface FormattingRulePayload {
  conditionField: string;
  conditionOperator: string;
  conditionValue: string | number | boolean | null;
  effect: string;
  effectSlot: string;
}

/** The panel view-model: which grid, its views, columns, and action set. */
export interface GridPanelPayload {
  gridId: string;
  title: string;
  views: GridViewPayload[];
  /** The user's last-displayed view for this grid (REQ-031, long-term). */
  activeViewKey: string;
  columns: GridColumnPayload[];
  /** The view's REQ-045 rules, in evaluation order (see ./formatting.ts). */
  formattingRules: FormattingRulePayload[];
  actions: ActionPayload[];
  /** The two most common actions, rendered as buttons (REQ-021). */
  commonActionKeys: string[];
  recentSearches: string[];
}

export interface GridRowPayload {
  recordId: string;
  title: string;
  values: Record<string, string | number | null>;
}

/** One keyset page (DB-S8): rows plus the cursor for the next page. */
export interface GridRowsPayload {
  rows: GridRowPayload[];
  nextCursor: string | null;
}

/** Whole-filtered-set truth, issued in parallel with the rows (REQ-026). */
export interface GridAggregatesPayload {
  totalCount: number;
  /** The same view WITHOUT search/ad-hoc narrowing — the "N rows hidden" gap. */
  unnarrowedCount: number;
  footer: Record<string, string>;
}
