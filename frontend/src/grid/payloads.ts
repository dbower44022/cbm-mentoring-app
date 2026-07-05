/**
 * Wire shapes for the grid panel (WTK-048), following the api/payloads.ts
 * precedent: hand-maintained mirrors of the server view-models until the
 * generated schema covers them. The shapes address the WTK-042 endpoint
 * contracts (`GET /grids/{gridId}/rows`, `GET /grids/{gridId}/aggregates`)
 * and the WTK-043 panel design (`ui/grid_panel.py`).
 */

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
}

export interface GridViewPayload {
  viewKey: string;
  label: string;
  /** What the view shows, in words — cited by the empty-view state. */
  criteria: string;
  isSystemView: boolean;
  allowAdHocFilters: boolean;
}

/** The panel view-model: which grid, its views, columns, and action set. */
export interface GridPanelPayload {
  gridId: string;
  title: string;
  views: GridViewPayload[];
  /** The user's last-displayed view for this grid (REQ-031, long-term). */
  activeViewKey: string;
  columns: GridColumnPayload[];
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
