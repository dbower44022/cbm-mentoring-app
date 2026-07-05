/**
 * The grid interaction model's parity tests: each case pins a behavior the
 * design in `ui/grid_panel.py` (and its pytest suite) declares, so drift
 * between the Python source of behavior and this browser mirror fails here.
 */

import { describe, expect, it } from "vitest";

import {
  actionMenus,
  bindingFor,
  destructiveConfirmation,
  EMPTY_SELECTION,
  HELP_ACTION,
  invalidInvocation,
  isModified,
  markModified,
  multiSelectActive,
  rememberSearch,
  resolveGridState,
  rowCountLabel,
  searchIsLive,
  selectView,
  sortBadgeFor,
  sortClick,
  sortShiftClick,
  toggleSelected,
} from "./model";
import { type ActionPayload } from "./payloads";

const REMOVE: ActionPayload = {
  key: "remove",
  label: "Remove",
  selectionContract: "multiple",
  classification: "destructive",
};
const OPEN: ActionPayload = {
  key: "open",
  label: "Open",
  selectionContract: "single",
  classification: "safe",
};
const EXPORT: ActionPayload = {
  key: "export",
  label: "Export",
  selectionContract: "none",
  classification: "safe",
};

describe("search (REQ-020)", () => {
  it("arms at the 3rd character, ignoring surrounding whitespace", () => {
    expect(searchIsLive("ab")).toBe(false);
    expect(searchIsLive("  ab ")).toBe(false);
    expect(searchIsLive("abc")).toBe(true);
  });

  it("remembers the last five armed searches, newest first, deduped", () => {
    let history: string[] = [];
    for (const text of ["alpha", "beta", "gamma", "delta", "echo", "foxtrot"]) {
      history = rememberSearch(history, text);
    }
    expect(history).toEqual(["foxtrot", "echo", "delta", "gamma", "beta"]);
    expect(rememberSearch(history, "delta")[0]).toBe("delta");
    expect(rememberSearch(history, "ab")).toEqual(history);
  });
});

describe("view selector modified flag (REQ-031)", () => {
  it("marks once per kind and clears on view selection", () => {
    let view = selectView("v1");
    expect(isModified(view)).toBe(false);
    view = markModified(view, "sort");
    view = markModified(view, "sort");
    expect(view.modifications).toEqual(["sort"]);
    expect(isModified(view)).toBe(true);
    view = selectView("v2");
    expect(isModified(view)).toBe(false);
  });
});

describe("header sorting (REQ-025)", () => {
  it("click is sole sort; repeat toggles direction", () => {
    let keys = sortClick([], "name");
    expect(keys).toEqual([{ fieldName: "name", descending: false }]);
    keys = sortClick(keys, "name");
    expect(keys).toEqual([{ fieldName: "name", descending: true }]);
    keys = sortClick(keys, "age");
    expect(keys).toEqual([{ fieldName: "age", descending: false }]);
  });

  it("shift+click appends, toggles, then removes; badges are 1-based", () => {
    let keys = sortClick([], "name");
    keys = sortShiftClick(keys, "age");
    expect(sortBadgeFor(keys, "age")).toEqual({ direction: "asc", position: 2 });
    keys = sortShiftClick(keys, "age");
    expect(sortBadgeFor(keys, "age")).toEqual({ direction: "desc", position: 2 });
    keys = sortShiftClick(keys, "age");
    expect(sortBadgeFor(keys, "age")).toBeNull();
    expect(sortBadgeFor(keys, "name")).toEqual({ direction: "asc", position: 1 });
  });
});

describe("selection (REQ-023)", () => {
  it("toggles explicit rows and reveals multi-select past one row", () => {
    let selection = toggleSelected(EMPTY_SELECTION, "r1");
    expect(multiSelectActive(selection)).toBe(false);
    selection = toggleSelected(selection, "r2");
    expect(multiSelectActive(selection)).toBe(true);
    selection = toggleSelected(selection, "r1");
    expect(selection).toEqual({ kind: "explicit", recordIds: ["r2"] });
  });

  it("select-all means the entire filtered set", () => {
    expect(multiSelectActive({ kind: "filteredSet" })).toBe(true);
    expect(bindingFor("Ctrl+A", "rows")).toBe("selectEntireFilteredSet");
  });
});

describe("the one keyboard model (REQ-024)", () => {
  it("disambiguates Enter by context and answers grid-wide keys anywhere", () => {
    expect(bindingFor("Enter", "rows")).toBe("openFocusedRecord");
    expect(bindingFor("Enter", "columnHeader")).toBe("sortColumn");
    expect(bindingFor("/", "columnHeader")).toBe("focusSearchBox");
    expect(bindingFor("Shift+F10", "rows")).toBe("openActionsMenu");
    expect(bindingFor("F1", "rows")).toBeNull();
  });
});

describe("actions: never hide (REQ-021/022)", () => {
  it("lays out two common buttons and one full menu with Help last", () => {
    const menus = actionMenus([OPEN, REMOVE, EXPORT], ["open", "export"]);
    expect(menus.buttons.map((a) => a.key)).toEqual(["open", "export"]);
    expect(menus.menu.map((a) => a.key)).toEqual(["open", "export", "remove", "Help"]);
    expect(menus.menu.at(-1)).toEqual(HELP_ACTION);
  });

  it("explains an invalid invocation instead of hiding the action", () => {
    expect(invalidInvocation(OPEN, 0)?.why).toContain("exactly one record");
    expect(invalidInvocation(OPEN, 2)?.why).toContain("2 rows are selected");
    expect(invalidInvocation(REMOVE, 0)?.whatNext).toContain("Select at least one row");
    expect(invalidInvocation(REMOVE, 3)).toBeNull();
    expect(invalidInvocation(EXPORT, 0)).toBeNull();
  });

  it("builds the exact-count, honest destructive confirmation", () => {
    const titles = ["A", "B", "C", "D", "E", "F", "G"];
    const confirmation = destructiveConfirmation(REMOVE, titles, 2);
    expect(confirmation.title).toBe("Remove 7 records?");
    expect(confirmation.listedTitles).toEqual(["A", "B", "C", "D", "E"]);
    expect(confirmation.moreCount).toBe(2);
    expect(confirmation.hiddenRowsNotice).toContain("2 selected rows are hidden");
    expect(confirmation.honestyNote).toContain("an administrator can restore them");
  });
});

describe("status bar (REQ-023/026)", () => {
  it("counts the whole filtered set with the keep-with-notice variant", () => {
    expect(rowCountLabel(1, 0, 0)).toBe("1 row");
    expect(rowCountLabel(200, 0, 0)).toBe("200 rows");
    expect(rowCountLabel(200, 10, 0)).toBe("200 rows, 10 Selected");
    expect(rowCountLabel(200, 10, 3)).toBe(
      "200 rows, 10 Selected (3 not in current filter)",
    );
  });
});

describe("the four grid states (REQ-030)", () => {
  const base = {
    viewLabel: "Needs follow-up",
    viewCriteria: "engagements needing follow-up",
    filteredCount: 0,
    unnarrowedCount: 200,
    searchText: "",
    adHocFilterCount: 0,
    loadError: null,
    permissionMissing: null,
  };

  it("rows showing resolves to no state", () => {
    expect(resolveGridState({ ...base, filteredCount: 5 })).toBeNull();
  });

  it("refusal outranks error outranks emptiness", () => {
    const both = resolveGridState({
      ...base,
      loadError: "boom",
      permissionMissing: "engagements.read",
    });
    expect(both?.kind).toBe("permissionRefusal");
    const error = resolveGridState({ ...base, loadError: "boom" });
    expect(error?.kind).toBe("dataSourceError");
    expect(error?.affordances).toContain("retry");
    expect(error?.detail).toBe("boom");
  });

  it("splits filtered-to-zero from truly empty", () => {
    const filtered = resolveGridState({ ...base, searchText: "acme" });
    expect(filtered?.kind).toBe("zeroFilteredSearch");
    expect(filtered?.message.whatHappened).toBe("No rows match 'acme'.");
    expect(filtered?.message.why).toContain("200 rows are");
    const empty = resolveGridState(base);
    expect(empty?.kind).toBe("emptyView");
    expect(empty?.message.why).toContain("'Needs follow-up'");
  });
});
