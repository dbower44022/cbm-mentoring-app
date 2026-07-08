/**
 * Grid interaction model (WTK-048): the browser-side execution of the grid
 * design in `src/mentorapp/ui/grid_panel.py` (WTK-043) — that module stays
 * the single source of behavior; every constant, message, and precedence
 * rule here mirrors it verbatim (REQ-016, REQ-020..REQ-031). Keystrokes,
 * sort clicks, and selection can't round-trip to the server, so this is the
 * one sanctioned client copy; parity drift is a defect against grid_panel.py.
 */

import { type EducatePayload } from "../api/payloads";
import { type ActionPayload } from "./payloads";

// --- Search (REQ-020): arms at the 3rd character, remembers the last 5 ----

export const MIN_SEARCH_LENGTH = 3;
export const RECENT_SEARCH_LIMIT = 5;

export function searchIsLive(searchText: string): boolean {
  return searchText.trim().length >= MIN_SEARCH_LENGTH;
}

/** Mirror of grid_surface.remember_search: newest first, deduped, capped. */
export function rememberSearch(
  previous: readonly string[],
  searchText: string,
): string[] {
  const armed = searchText.trim();
  if (armed.length < MIN_SEARCH_LENGTH) {
    return [...previous];
  }
  const rest = previous.filter((entry) => entry !== armed);
  return [armed, ...rest].slice(0, RECENT_SEARCH_LIMIT);
}

// --- View selector & the modified flag -------------------------------------

/** Search is deliberately absent: it is session state, never view state. */
export const VIEW_MODIFICATION_KINDS = [
  "sort",
  "adHocFilter",
  "columnWidth",
  "viewSettings",
] as const;

export type ViewModificationKind = (typeof VIEW_MODIFICATION_KINDS)[number];

export interface ViewSelectorState {
  activeViewKey: string;
  modifications: readonly ViewModificationKind[];
}

export function isModified(state: ViewSelectorState): boolean {
  return state.modifications.length > 0;
}

export function markModified(
  state: ViewSelectorState,
  kind: ViewModificationKind,
): ViewSelectorState {
  if (state.modifications.includes(kind)) {
    return state;
  }
  return { ...state, modifications: [...state.modifications, kind] };
}

/** Switching views applies instantly and discards temporary modifications. */
export function selectView(viewKey: string): ViewSelectorState {
  return { activeViewKey: viewKey, modifications: [] };
}

// --- Sorting: header clicks, arrow + position badge (REQ-025) ---------------

export interface SortKey {
  fieldName: string;
  descending: boolean;
}

export interface SortBadge {
  direction: "asc" | "desc";
  position: number;
}

/** Plain click: sole sort; a repeat on the sole key toggles its direction. */
export function sortClick(keys: readonly SortKey[], fieldName: string): SortKey[] {
  const sole = keys.length === 1 ? keys[0] : undefined;
  if (sole?.fieldName === fieldName) {
    return [{ fieldName, descending: !sole.descending }];
  }
  return [{ fieldName, descending: false }];
}

/** Shift+click: append secondary/tertiary; toggle in place; third click removes. */
export function sortShiftClick(keys: readonly SortKey[], fieldName: string): SortKey[] {
  const existing = keys.find((key) => key.fieldName === fieldName);
  if (existing === undefined) {
    return [...keys, { fieldName, descending: false }];
  }
  if (existing.descending) {
    return keys.filter((key) => key.fieldName !== fieldName);
  }
  return keys.map((key) =>
    key.fieldName === fieldName ? { fieldName, descending: true } : key,
  );
}

export function sortBadgeFor(
  keys: readonly SortKey[],
  fieldName: string,
): SortBadge | null {
  const position = keys.findIndex((key) => key.fieldName === fieldName);
  const key = keys[position];
  if (key === undefined) {
    return null;
  }
  return { direction: key.descending ? "desc" : "asc", position: position + 1 };
}

// --- Selection (REQ-023): entire-filtered-set select-all, keep-with-notice --

/**
 * Explicit rows, or the ENTIRE filtered result set (Ctrl+A / select-all is
 * never "visible rows first") — the wire vocabulary of
 * grid_surface.parse_selection.
 */
export type Selection =
  { kind: "explicit"; recordIds: readonly string[] } | { kind: "filteredSet" };

export const EMPTY_SELECTION: Selection = { kind: "explicit", recordIds: [] };

export function toggleSelected(selection: Selection, recordId: string): Selection {
  if (selection.kind === "filteredSet") {
    // Ctrl+A then Space narrows back to explicit rows minus the toggled one:
    // the only honest reading without the full id list client-side.
    return { kind: "explicit", recordIds: [] };
  }
  const recordIds = selection.recordIds.includes(recordId)
    ? selection.recordIds.filter((id) => id !== recordId)
    : [...selection.recordIds, recordId];
  return { kind: "explicit", recordIds };
}

export function selectedCount(selection: Selection, filteredCount: number): number {
  return selection.kind === "filteredSet" ? filteredCount : selection.recordIds.length;
}

/** Multi-select (and its checkbox column) reveals past a single row. */
export function multiSelectActive(selection: Selection): boolean {
  return selection.kind === "filteredSet" || selection.recordIds.length > 1;
}

// --- The one keyboard model (REQ-024) ---------------------------------------

export const INITIAL_FOCUS = "searchBox";

export type KeyContext = "rows" | "columnHeader" | "grid";

interface KeyBinding {
  keys: readonly string[];
  action: string;
  context: KeyContext;
}

export const GRID_KEYBOARD_MODEL: readonly KeyBinding[] = [
  { keys: ["ArrowUp", "ArrowDown"], action: "moveRowFocus", context: "rows" },
  { keys: ["Space"], action: "toggleSelection", context: "rows" },
  {
    keys: ["Shift+ArrowUp", "Shift+ArrowDown"],
    action: "extendSelection",
    context: "rows",
  },
  { keys: ["Ctrl+A"], action: "selectEntireFilteredSet", context: "grid" },
  { keys: ["Enter"], action: "openFocusedRecord", context: "rows" },
  { keys: ["ContextMenu", "Shift+F10"], action: "openActionsMenu", context: "grid" },
  { keys: ["/"], action: "focusSearchBox", context: "grid" },
  { keys: ["Enter"], action: "sortColumn", context: "columnHeader" },
];

/** Resolve one keypress; grid-wide bindings apply in every context. */
export function bindingFor(key: string, context: KeyContext): string | null {
  for (const binding of GRID_KEYBOARD_MODEL) {
    if (
      binding.keys.includes(key) &&
      (binding.context === context || binding.context === "grid")
    ) {
      return binding.action;
    }
  }
  return null;
}

// --- Actions: never hide, never disable (REQ-021/022) -----------------------

export const HELP_ACTION: ActionPayload = {
  key: "Help",
  label: "Help",
  selectionContract: "none",
  classification: "safe",
};

/**
 * The Edit action (ui/edit_form.py EDIT_RECORD, REQ-032): modifying, exactly
 * one row. Joins any grid whose active view names an app entity — the form
 * itself opens in the record's pinned pop-out window, so the grid keeps
 * working (REQ-012's keep-working rule applies to edit entry too).
 */
export const EDIT_RECORD_ACTION: ActionPayload = {
  key: "EditRecord",
  label: "Edit",
  selectionContract: "single",
  classification: "modifying",
};

export const CONFIRMATION_LISTED_LIMIT = 5;

/** Honest soft-delete wording (system-wide rule): never claim permanence. */
export const SOFT_DELETE_HONESTY =
  "Records are removed from all views; an administrator can restore them.";

export interface ActionMenus {
  buttons: ActionPayload[];
  menu: ActionPayload[];
}

/**
 * Two common buttons + the ONE full menu serving both the Other Actions
 * dropdown and the right-click menu — common actions lead, Help always last.
 * `appended` is the data source's registered workprocesses (REQ-041): they
 * join the same menu after the grid's own actions — present in BOTH menu
 * paths, never hidden or disabled — with Help still closing the list.
 */
export function actionMenus(
  actions: readonly ActionPayload[],
  commonKeys: readonly string[],
  appended: readonly ActionPayload[] = [],
): ActionMenus {
  const named = commonKeys
    .map((key) => actions.find((action) => action.key === key))
    .filter((action): action is ActionPayload => action !== undefined);
  // REQ-021: the action bar always carries the data set's two most common
  // actions. When the payload names none, the first two domain actions
  // stand in — an empty button row is a hidden-actions smell (FND-909 D6).
  const buttons = named.length > 0 ? named : appended.slice(0, 2);
  const rest = actions.filter((action) => !commonKeys.includes(action.key));
  const menuAppended = appended.filter((action) => !buttons.includes(action));
  return { buttons, menu: [...buttons, ...rest, ...menuAppended, HELP_ACTION] };
}

/** The never-hide explainer: why THIS invocation can't run, or null. */
export function invalidInvocation(
  action: ActionPayload,
  count: number,
): EducatePayload | null {
  if (action.selectionContract === "single" && count !== 1) {
    const state =
      count === 0 ? "no row is selected" : `${String(count)} rows are selected`;
    return {
      whatHappened: `'${action.label}' didn't run.`,
      why: `'${action.label}' works on exactly one record, and ${state}.`,
      whatNext: "Select a single row and run the action again.",
    };
  }
  if (action.selectionContract === "multiple" && count === 0) {
    return {
      whatHappened: `'${action.label}' didn't run.`,
      why: `'${action.label}' acts on the selected rows, and none is selected.`,
      whatNext:
        "Select at least one row (Space, or Shift/Ctrl+click) and run the action again.",
    };
  }
  return null;
}

export interface ActionConfirmation {
  title: string;
  listedTitles: string[];
  moreCount: number;
  hiddenRowsNotice: string | null;
  honestyNote: string | null;
}

/**
 * The one shared confirmation shape (REQ-022): the action, the EXACT count,
 * the first records + "and X more", the selected-but-filtered-out rows.
 */
export function destructiveConfirmation(
  action: ActionPayload,
  recordTitles: readonly string[],
  hiddenSelectedCount: number,
): ActionConfirmation {
  const count = recordTitles.length;
  const items = count === 1 ? "1 record" : `${String(count)} records`;
  const hidden =
    hiddenSelectedCount > 0
      ? `${String(hiddenSelectedCount)} selected ${
          hiddenSelectedCount === 1 ? "row is" : "rows are"
        } hidden by the current filter; '${action.label}' still applies to ${
          hiddenSelectedCount === 1 ? "it" : "them"
        }.`
      : null;
  return {
    title: `${action.label} ${items}?`,
    listedTitles: recordTitles.slice(0, CONFIRMATION_LISTED_LIMIT),
    moreCount: Math.max(0, count - CONFIRMATION_LISTED_LIMIT),
    hiddenRowsNotice: hidden,
    honestyNote: action.classification === "destructive" ? SOFT_DELETE_HONESTY : null,
  };
}

// --- Status bar (REQ-023/026) ------------------------------------------------

/** The status-bar right side over the WHOLE filtered set, never the window. */
export function rowCountLabel(
  totalRows: number,
  selected: number,
  hiddenSelectedCount: number,
): string {
  const rows = totalRows === 1 ? "1 row" : `${String(totalRows)} rows`;
  if (selected === 0) {
    return rows;
  }
  const label = `${rows}, ${String(selected)} Selected`;
  return hiddenSelectedCount > 0
    ? `${label} (${String(hiddenSelectedCount)} not in current filter)`
    : label;
}

// --- The four distinguished grid states (REQ-030) ----------------------------

export type GridStateKind =
  "emptyView" | "zeroFilteredSearch" | "dataSourceError" | "permissionRefusal";

export interface GridStateNotice {
  kind: GridStateKind;
  message: EducatePayload;
  affordances: readonly string[];
  detail: string | null;
}

export interface GridStateInputs {
  viewLabel: string;
  viewCriteria: string;
  filteredCount: number;
  unnarrowedCount: number;
  searchText: string;
  adHocFilterCount: number;
  loadError: string | null;
  permissionMissing: string | null;
  permissionGrantor?: string;
}

/**
 * Which of the four states renders, or null (rows showing). Precedence
 * mirrors grid_panel.resolve_grid_state: refusal > error > emptiness, and
 * narrowing splits filtered-to-zero from truly empty so a filter never
 * masquerades as missing data.
 */
export function resolveGridState(inputs: GridStateInputs): GridStateNotice | null {
  if (inputs.permissionMissing !== null) {
    const grantor = inputs.permissionGrantor ?? "a system administrator";
    return {
      kind: "permissionRefusal",
      message: {
        whatHappened: "This grid can't be shown.",
        why: `Your account doesn't have the '${inputs.permissionMissing}' data-source permission this view reads from.`,
        whatNext: `Ask ${grantor} to grant it — access is per data source.`,
      },
      affordances: [],
      detail: null,
    };
  }
  if (inputs.loadError !== null) {
    return {
      kind: "dataSourceError",
      message: {
        whatHappened: "This grid couldn't load its data.",
        why: "The data source didn't answer correctly.",
        whatNext:
          "Retry now — if it keeps failing, the technical detail helps an administrator find the cause.",
      },
      affordances: ["retry", "showDetail"],
      detail: inputs.loadError,
    };
  }
  if (inputs.filteredCount > 0) {
    return null;
  }
  const narrowed = searchIsLive(inputs.searchText) || inputs.adHocFilterCount > 0;
  if (narrowed) {
    const hidden = inputs.unnarrowedCount;
    const rows = hidden === 1 ? "1 row is" : `${String(hidden)} rows are`;
    const needle = searchIsLive(inputs.searchText)
      ? `'${inputs.searchText.trim()}'`
      : "the current column filters";
    return {
      kind: "zeroFilteredSearch",
      message: {
        whatHappened: `No rows match ${needle}.`,
        why: `${rows} in this view but hidden by your search or column filters — the data is still there.`,
        whatNext: "Clear the search or the column filters to see them.",
      },
      affordances: ["clearSearch", "clearFilters"],
      detail: null,
    };
  }
  return {
    kind: "emptyView",
    message: {
      whatHappened: "There's nothing in this view yet.",
      why: `'${inputs.viewLabel}' shows ${inputs.viewCriteria}, and no records match right now.`,
      whatNext:
        "Records appear here the moment they match — or switch views to see more of this data set.",
    },
    affordances: [],
    detail: null,
  };
}
