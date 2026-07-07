/**
 * @vitest-environment jsdom
 *
 * Interaction tests for the RENDERED grid panel (WTK-052): where
 * model.test.ts pins the pure interaction model against its Python source of
 * behavior, this suite drives grid-panel.tsx the way a user does — clicks,
 * keystrokes, scrolls — against a stubbed fetch speaking the real envelope,
 * and asserts the three-region anatomy, infinite scroll, live search with
 * history, the modified-view flag, never-hidden actions with educate-voice
 * refusals, keep-with-notice selection, sort badges, the full keyboard
 * model, and the four distinguished grid states as actually rendered
 * (REQ-016, REQ-020..REQ-031).
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GridPanel } from "./grid-panel";
import {
  type ActionPayload,
  type GridPanelPayload,
  type GridRowPayload,
} from "./payloads";

// React 18 warns state updates outside act() unless the environment opts in.
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// --- The stub server: the real envelope shape over a routed fetch -----------

function ok(data: unknown): unknown {
  return { data, meta: {}, errors: null };
}

function refusal(code: string, message: string): unknown {
  return { data: null, meta: {}, errors: [{ fieldName: null, code, message }] };
}

const OPEN: ActionPayload = {
  key: "open",
  label: "Open",
  selectionContract: "single",
  classification: "safe",
};
const REMOVE: ActionPayload = {
  key: "remove",
  label: "Remove",
  selectionContract: "multiple",
  classification: "destructive",
};
const EXPORT: ActionPayload = {
  key: "export",
  label: "Export",
  selectionContract: "none",
  classification: "safe",
};

function makePanel(): GridPanelPayload {
  return {
    gridId: "engagements",
    title: "Engagements",
    views: [
      {
        viewKey: "v-all",
        label: "All engagements",
        criteria: "every engagement",
        dataSourceKey: "engagements",
        isSystemView: true,
        allowAdHocFilters: true,
      },
      {
        viewKey: "v-follow",
        label: "Needs follow-up",
        criteria: "engagements needing follow-up",
        dataSourceKey: "engagements",
        isSystemView: true,
        allowAdHocFilters: true,
      },
    ],
    activeViewKey: "v-all",
    columns: [
      { fieldName: "name", label: "Name", format: "text", alignment: "left" },
      { fieldName: "stage", label: "Stage", format: "text", alignment: "left" },
      {
        fieldName: "nextSessionAt",
        label: "Next Session",
        format: "datetime",
        alignment: "left",
      },
    ],
    formattingRules: [],
    actions: [OPEN, REMOVE, EXPORT],
    commonActionKeys: ["open", "export"],
    recentSearches: [],
  };
}

function makeRows(from: number, to: number): GridRowPayload[] {
  return Array.from({ length: to - from + 1 }, (_, index) => {
    const n = from + index;
    return {
      recordId: `r${String(n)}`,
      title: `Engagement ${String(n)}`,
      values: {
        name: `Engagement ${String(n)}`,
        stage: "Active",
        // Raw wire form (str(datetime) with microseconds) — the datetime
        // column proves cells render through the formatter, never raw (D1).
        nextSessionAt: n % 2 === 0 ? null : "2026-07-10 10:00:00.000000",
      },
    };
  });
}

interface ServerConfig {
  panel?: GridPanelPayload;
  rows?: (url: URL) => unknown;
  aggregates?: (url: URL) => unknown;
  /** The data source's workprocess action list (REQ-041); default none. */
  workprocessActions?: unknown[];
  /** Routes the four /workprocesses/runs verbs when a test drives a run. */
  workprocessRun?: (url: URL, init: RequestInit | undefined) => unknown;
  /** The GET /help/resolve answer (REQ-043); default a mapped page URL. */
  helpResolution?: unknown;
}

const DEFAULT_AGGREGATES = { totalCount: 200, unnarrowedCount: 200, footer: {} };

/** Serve the grid + workprocess endpoints; requests are logged for assertions. */
function renderGrid(config: ServerConfig = {}): { calls: URL[] } {
  const calls: URL[] = [];
  // The component always calls fetch with a path string; typing the stub
  // that way keeps String(input) well-defined.
  vi.stubGlobal(
    "fetch",
    (input: string | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://grid.test");
      calls.push(url);
      let body: unknown;
      if (url.pathname.endsWith("/grid")) {
        body = ok(config.panel ?? makePanel());
      } else if (url.pathname.includes("/rows")) {
        body = (config.rows ?? (() => ok({ rows: makeRows(1, 25), nextCursor: null })))(
          url,
        );
      } else if (url.pathname.includes("/aggregates")) {
        body = (config.aggregates ?? (() => ok(DEFAULT_AGGREGATES)))(url);
      } else if (url.pathname.startsWith("/workprocesses/actions/")) {
        body = ok(config.workprocessActions ?? []);
      } else if (url.pathname.startsWith("/workprocesses/runs")) {
        if (config.workprocessRun === undefined) {
          throw new Error(`Unrouted run request: ${url.pathname}`);
        }
        body = config.workprocessRun(url, init);
      } else if (url.pathname === "/help/resolve") {
        body =
          config.helpResolution ??
          ok({ url: "https://docs.example.org/help/x", mapped: true, notice: null });
      } else {
        throw new Error(`Unrouted request: ${url.pathname}`);
      }
      return Promise.resolve({
        status: 200,
        json: () => Promise.resolve(body),
      } as unknown as Response);
    },
  );
  render(<GridPanel panelKey="engagements" />);
  return { calls };
}

function element(selector: string): HTMLElement {
  const found = document.querySelector(selector);
  if (!(found instanceof HTMLElement)) {
    throw new Error(`Expected an element at: ${selector}`);
  }
  return found;
}

function lastRowsQuery(calls: URL[]): URLSearchParams {
  const rowCalls = calls.filter((url) => url.pathname.includes("/rows"));
  const last = rowCalls.at(-1);
  if (last === undefined) {
    throw new Error("No /rows request was made");
  }
  return last.searchParams;
}

function rowFor(title: string): HTMLElement {
  const row = screen.getByText(title).closest("tr");
  if (row === null) {
    throw new Error(`No table row holds: ${title}`);
  }
  return row;
}

// --- REQ-016: the three stacked regions --------------------------------------

describe("anatomy (REQ-016)", () => {
  it("stacks action bar, table, and status bar; count and footer speak the whole set", async () => {
    renderGrid({
      aggregates: () =>
        ok({ totalCount: 200, unnarrowedCount: 200, footer: { name: "200 total" } }),
    });
    await screen.findByText("Engagement 1");

    const regions = [...element(".grid-panel").children].map((el) => el.className);
    expect(regions).toEqual(["grid-action-bar", "grid-table-host", "grid-status-bar"]);

    // Action bar thirds: view machinery left, search middle, actions right.
    const bar = screen.getByRole("toolbar", { name: "Grid actions" });
    expect(bar.contains(screen.getByLabelText("View"))).toBe(true);
    expect(bar.contains(screen.getByLabelText("Search displayed columns"))).toBe(true);
    expect(bar.contains(screen.getByRole("button", { name: "Other Actions" }))).toBe(
      true,
    );

    // 25 rows are loaded, but count and footer aggregate the full set.
    expect(screen.getByText("200 rows")).toBeTruthy();
    expect(screen.getByText("200 total").closest("tfoot")).not.toBeNull();
  });

  it("starts focus in the search box", async () => {
    renderGrid();
    const search = await screen.findByLabelText("Search displayed columns");
    expect(document.activeElement).toBe(search);
  });
});

// --- FND-909 D1: cells render through the one formatter, never raw ------------

describe("cell value formatting (FND-909 D1)", () => {
  it("renders datetime cells in the prototype flavor and absent values as dashes", async () => {
    renderGrid();
    await screen.findByText("Engagement 1");

    // Odd rows carry the raw SQL form; it must reach the user formatted.
    expect(rowFor("Engagement 1").textContent).toContain("Jul 10, 10:00 AM");
    expect(rowFor("Engagement 2").textContent).toContain("—");
    expect(document.body.textContent).not.toContain("2026-07-10 10:00:00.000000");
  });
});

describe("conditional formatting (REQ-045, FND-909 D7)", () => {
  it("renders a matched status cell as a slot-colored chip, not plain text", async () => {
    const panel = makePanel();
    // The served rule shape: standard operator, accent effect, a status
    // slot — never a literal color (FND-906).
    panel.formattingRules = [
      {
        conditionField: "stage",
        conditionOperator: "equals",
        conditionValue: "Active",
        effect: "accent",
        effectSlot: "statusPositive",
      },
    ];
    renderGrid({ panel });
    await screen.findByText("Engagement 1");

    const cell = rowFor("Engagement 1").querySelectorAll("td")[1];
    const chip = cell?.querySelector(".status-chip.slot-colored");
    if (!(chip instanceof HTMLElement)) {
      throw new Error("The matched status cell did not render a chip");
    }
    expect(chip.textContent).toBe("Active");
    // The chip's color rides the theme's status-slot custom property — the
    // slotCssVariable derivation, so the active template paints it.
    expect(chip.style.getPropertyValue("--chip-color")).toBe(
      "var(--slot-status-positive)",
    );
    // Unruled cells stay plain: no chip around the name column.
    expect(rowFor("Engagement 1").querySelectorAll(".status-chip").length).toBe(1);
  });
});

// --- REQ-020: live search + recent-search history -----------------------------

describe("search (REQ-020)", () => {
  it("arms at the 3rd character as a server-side narrowing", async () => {
    const { calls } = renderGrid();
    await screen.findByText("Engagement 1");
    const search = screen.getByLabelText("Search displayed columns");

    fireEvent.change(search, { target: { value: "ac" } });
    await waitFor(() => {
      expect(lastRowsQuery(calls).get("search")).toBeNull();
    });

    fireEvent.change(search, { target: { value: "acme" } });
    await waitFor(() => {
      expect(lastRowsQuery(calls).get("search")).toBe("acme");
    });
    // Search narrows the view's own results — the view is still the query.
    expect(lastRowsQuery(calls).get("view")).toBe("v-all");
  });

  it("remembers the last five armed searches, newest first, deduped", async () => {
    renderGrid();
    await screen.findByText("Engagement 1");
    const search = screen.getByLabelText("Search displayed columns");

    for (const text of [
      "alpha",
      "beta",
      "gamma",
      "delta",
      "echo",
      "foxtrot",
      "delta",
    ]) {
      fireEvent.change(search, { target: { value: text } });
    }
    const options = [...document.querySelectorAll("datalist option")].map((option) =>
      option.getAttribute("value"),
    );
    expect(options).toEqual(["delta", "foxtrot", "echo", "gamma", "beta"]);
  });
});

// --- Infinite scroll (REQ-024's edge-loading, no pagination ever) -------------

describe("infinite scroll", () => {
  const pagedRows = (url: URL): unknown =>
    url.searchParams.get("cursor") === "c25"
      ? ok({ rows: makeRows(26, 50), nextCursor: null })
      : ok({ rows: makeRows(1, 25), nextCursor: "c25" });

  it("appends the next keyset page as the scroll nears the bottom edge", async () => {
    const { calls } = renderGrid({ rows: pagedRows });
    await screen.findByText("Engagement 25");
    expect(screen.queryByText("Engagement 26")).toBeNull();

    const host = element(".grid-table-host");
    Object.defineProperties(host, {
      scrollTop: { value: 800, configurable: true },
      clientHeight: { value: 200, configurable: true },
      scrollHeight: { value: 1100, configurable: true },
    });
    fireEvent.scroll(host);

    await screen.findByText("Engagement 26");
    expect(lastRowsQuery(calls).get("cursor")).toBe("c25");
    // Appended, not replaced — and the count never follows the window.
    expect(screen.getByText("Engagement 1")).toBeTruthy();
    expect(screen.getByText("200 rows")).toBeTruthy();
  });

  it("row focus auto-loads at the bottom edge so arrows never dead-end", async () => {
    const { calls } = renderGrid({ rows: pagedRows });
    await screen.findByText("Engagement 25");
    const host = element(".grid-table-host");

    // 25 loaded rows, load-ahead 10: the 16th ArrowDown crosses the edge.
    for (let press = 0; press < 16; press += 1) {
      fireEvent.keyDown(host, { key: "ArrowDown" });
    }
    await screen.findByText("Engagement 26");
    expect(lastRowsQuery(calls).get("cursor")).toBe("c25");
  });
});

// --- REQ-025/REQ-031: header sorting, badges, and the modified flag -----------

describe("header sorting and the modified view flag (REQ-025, REQ-031)", () => {
  it("shows direction arrows with numbered badges and speaks multi-sort", async () => {
    const { calls } = renderGrid();
    await screen.findByText("Engagement 1");
    const name = screen.getByRole("columnheader", { name: /Name/ });
    const stage = screen.getByRole("columnheader", { name: /Stage/ });

    fireEvent.click(name);
    expect(name.getAttribute("aria-sort")).toBe("ascending");
    expect(name.textContent).toContain("▲1");
    await waitFor(() => {
      expect(lastRowsQuery(calls).get("sort")).toBe("name:asc");
    });

    fireEvent.click(name);
    expect(name.getAttribute("aria-sort")).toBe("descending");
    expect(name.textContent).toContain("▼1");

    fireEvent.click(stage, { shiftKey: true });
    expect(stage.textContent).toContain("▲2");
    await waitFor(() => {
      expect(lastRowsQuery(calls).get("sort")).toBe("name:desc,stage:asc");
    });
  });

  it("flags the sorted view as modified until another view is selected", async () => {
    const { calls } = renderGrid();
    await screen.findByText("Engagement 1");

    fireEvent.click(screen.getByRole("columnheader", { name: /Name/ }));
    expect(
      screen.getByRole("option", { name: "All engagements (modified)" }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Save as my view" })).toBeTruthy();

    // Selecting a view applies instantly and discards the temporary sort.
    fireEvent.change(screen.getByLabelText("View"), { target: { value: "v-follow" } });
    await waitFor(() => {
      expect(lastRowsQuery(calls).get("view")).toBe("v-follow");
    });
    expect(lastRowsQuery(calls).get("sort")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save as my view" })).toBeNull();
    expect(screen.getByRole("option", { name: "Needs follow-up" })).toBeTruthy();
  });
});

// --- REQ-021/REQ-022: actions never hidden, refusals educate ------------------

describe("actions (REQ-021, REQ-022)", () => {
  it("serves the one full menu — common actions first, Help last — from both paths", async () => {
    renderGrid();
    await screen.findByText("Engagement 1");

    fireEvent.click(screen.getByRole("button", { name: "Other Actions" }));
    const menu = screen.getByRole("list", { name: "All actions" });
    const labels = [...menu.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["Open", "Export", "Remove", "Help"]);
    // Never disabled: every entry is invocable regardless of selection.
    expect(
      [...menu.querySelectorAll("button")].every((button) => !button.disabled),
    ).toBe(true);
    vi.spyOn(window, "open").mockReturnValue(null);
    fireEvent.click(screen.getByRole("button", { name: "Help" }));
    expect(screen.queryByRole("list", { name: "All actions" })).toBeNull();

    // Right-click opens the SAME full menu.
    fireEvent.contextMenu(element(".grid-panel"));
    expect(screen.getByRole("list", { name: "All actions" })).toBeTruthy();
  });

  it("wires the menu's Help to the one resolver with the data set identity", async () => {
    // REQ-043 via SKL-122: the grid's Help resolves its CONTENT — the active
    // view's data set — and a generic landing's notice shows dismissible.
    const { calls } = renderGrid({
      helpResolution: ok({
        url: "https://docs.example.org/help",
        mapped: false,
        notice: "No page-specific help exists yet for this data set.",
      }),
    });
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    await screen.findByText("Engagement 1");

    fireEvent.click(screen.getByRole("button", { name: "Other Actions" }));
    fireEvent.click(screen.getByRole("button", { name: "Help" }));

    const notice = await screen.findByText(/No page-specific help exists yet/);
    expect(notice).toBeTruthy();
    const resolve = calls.find((url) => url.pathname === "/help/resolve");
    expect(resolve?.searchParams.get("sourceType")).toBe("dataSet");
    expect(resolve?.searchParams.get("sourceIdentifier")).toBe("engagements");
    // A separate tab, never a navigation of the working grid window.
    expect(opened).toHaveBeenCalledWith(
      "https://docs.example.org/help",
      "_blank",
      "noopener",
    );
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/No page-specific help exists yet/)).toBeNull();
  });

  it("explains an invalid invocation in educate voice instead of hiding it", async () => {
    renderGrid();
    await screen.findByText("Engagement 1");

    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    const dialog = screen.getByRole("dialog", { name: "Why this didn't run" });
    expect(dialog.textContent).toContain("'Open' didn't run.");
    expect(dialog.textContent).toContain(
      "'Open' works on exactly one record, and no row is selected.",
    );
    expect(dialog.textContent).toContain(
      "Select a single row and run the action again.",
    );
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect(screen.queryByRole("dialog", { name: "Why this didn't run" })).toBeNull();
  });

  it("confirms destructive actions with the exact count, first records, and honest wording", async () => {
    renderGrid();
    await screen.findByText("Engagement 1");
    for (let n = 1; n <= 7; n += 1) {
      fireEvent.click(screen.getByText(`Engagement ${String(n)}`), { ctrlKey: true });
    }

    fireEvent.click(screen.getByRole("button", { name: "Other Actions" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    const dialog = screen.getByRole("dialog", { name: "Remove 7 records?" });
    expect(dialog.textContent).toContain("Engagement 5");
    expect(dialog.textContent).toContain("…and 2 more");
    // Honest soft-delete wording — never a "cannot be undone" claim.
    expect(dialog.textContent).toContain("an administrator can restore them");
    expect(dialog.textContent).not.toContain("cannot be undone");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog", { name: "Remove 7 records?" })).toBeNull();
  });
});

// --- REQ-023: selection keeps with notice; double-click opens -----------------

describe("selection (REQ-023)", () => {
  const searchable = {
    rows: (url: URL): unknown =>
      url.searchParams.get("search") === "zzz"
        ? ok({ rows: makeRows(50, 50), nextCursor: null })
        : ok({ rows: makeRows(1, 25), nextCursor: null }),
    aggregates: (url: URL): unknown =>
      url.searchParams.get("search") === "zzz"
        ? ok({ totalCount: 1, unnarrowedCount: 200, footer: {} })
        : ok(DEFAULT_AGGREGATES),
  };

  it("keeps the selection when search narrows it away, with the notice", async () => {
    renderGrid(searchable);
    await screen.findByText("Engagement 1");

    fireEvent.click(screen.getByText("Engagement 1"));
    expect(screen.getByText("200 rows, 1 Selected")).toBeTruthy();
    fireEvent.click(screen.getByText("Engagement 2"), { ctrlKey: true });
    expect(screen.getByText("200 rows, 2 Selected")).toBeTruthy();
    // Multi-select reveals the checkbox column.
    expect(screen.getAllByRole("checkbox").length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText("Search displayed columns"), {
      target: { value: "zzz" },
    });
    // Never silently deselected: both rows stay counted, flagged as hidden.
    await screen.findByText("1 row, 2 Selected (2 not in current filter)");
  });

  it("spells out hidden-selected rows in the destructive confirmation", async () => {
    renderGrid(searchable);
    await screen.findByText("Engagement 1");
    fireEvent.click(screen.getByText("Engagement 1"));
    fireEvent.click(screen.getByText("Engagement 2"), { ctrlKey: true });
    fireEvent.change(screen.getByLabelText("Search displayed columns"), {
      target: { value: "zzz" },
    });
    await screen.findByText("1 row, 2 Selected (2 not in current filter)");

    fireEvent.click(screen.getByRole("button", { name: "Other Actions" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    const dialog = screen.getByRole("dialog", { name: "Remove 2 records?" });
    expect(dialog.textContent).toContain(
      "2 selected rows are hidden by the current filter; 'Remove' still applies to them.",
    );
  });

  it("single click selects sole; double-click opens the record window", async () => {
    renderGrid();
    await screen.findByText("Engagement 1");
    const opened = vi.spyOn(window, "open").mockReturnValue(null);

    fireEvent.click(screen.getByText("Engagement 1"));
    fireEvent.click(screen.getByText("Engagement 2"));
    expect(rowFor("Engagement 2").getAttribute("aria-selected")).toBe("true");
    expect(rowFor("Engagement 1").getAttribute("aria-selected")).toBe("false");
    expect(screen.getByText("200 rows, 1 Selected")).toBeTruthy();

    fireEvent.dblClick(screen.getByText("Engagement 2"));
    expect(opened).toHaveBeenCalledWith("/records/grid/r2", "_blank");
  });
});

// --- REQ-024: the full no-mouse keyboard model ---------------------------------

describe("keyboard model (REQ-024)", () => {
  it("drives arrows, Space, Shift+arrows, Ctrl+A, Enter, Shift+F10, and /", async () => {
    renderGrid();
    await screen.findByText("Engagement 1");
    const host = element(".grid-table-host");
    const opened = vi.spyOn(window, "open").mockReturnValue(null);

    // Arrows move the visible row focus.
    fireEvent.keyDown(host, { key: "ArrowDown" });
    fireEvent.keyDown(host, { key: "ArrowDown" });
    expect(element("tr.grid-row-focused").textContent).toContain("Engagement 3");

    // Space toggles; Shift+Arrow extends and reveals the checkbox column.
    fireEvent.keyDown(host, { key: " " });
    expect(screen.getByText("200 rows, 1 Selected")).toBeTruthy();
    fireEvent.keyDown(host, { key: "ArrowDown", shiftKey: true });
    expect(screen.getByText("200 rows, 2 Selected")).toBeTruthy();
    expect(screen.getAllByRole("checkbox").length).toBeGreaterThan(0);

    // Ctrl+A selects the ENTIRE filtered set — 200, not the 25 loaded.
    fireEvent.keyDown(host, { key: "a", ctrlKey: true });
    expect(screen.getByText("200 rows, 200 Selected")).toBeTruthy();

    // Enter opens the focused record as a real window.
    fireEvent.keyDown(host, { key: "Enter" });
    expect(opened).toHaveBeenCalledWith("/records/grid/r4", "_blank");

    // Shift+F10 answers with the one full actions menu.
    fireEvent.keyDown(host, { key: "F10", shiftKey: true });
    expect(screen.getByRole("list", { name: "All actions" })).toBeTruthy();

    // "/" returns focus to the search box from anywhere in the grid.
    fireEvent.keyDown(host, { key: "/" });
    expect(document.activeElement).toBe(
      screen.getByLabelText("Search displayed columns"),
    );
  });

  it("sorts from a focused column header with Enter", async () => {
    renderGrid();
    await screen.findByText("Engagement 1");
    const name = screen.getByRole("columnheader", { name: /Name/ });
    fireEvent.keyDown(name, { key: "Enter" });
    expect(name.getAttribute("aria-sort")).toBe("ascending");
    expect(name.textContent).toContain("▲1");
  });
});

// --- REQ-030: the four distinguished grid states, as rendered ------------------

describe("the four grid states (REQ-030)", () => {
  it("renders the truly-empty view naming the view's criteria", async () => {
    renderGrid({
      rows: () => ok({ rows: [], nextCursor: null }),
      aggregates: () => ok({ totalCount: 0, unnarrowedCount: 0, footer: {} }),
    });
    await screen.findByText("There's nothing in this view yet.");
    expect(
      screen.getByText(
        "'All engagements' shows every engagement, and no records match right now.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Clear search" })).toBeNull();
  });

  it("never lets a search masquerade as missing data, and clears it in place", async () => {
    renderGrid({
      rows: (url) =>
        url.searchParams.has("search")
          ? ok({ rows: [], nextCursor: null })
          : ok({ rows: makeRows(1, 25), nextCursor: null }),
      aggregates: (url) =>
        url.searchParams.has("search")
          ? ok({ totalCount: 0, unnarrowedCount: 200, footer: {} })
          : ok(DEFAULT_AGGREGATES),
    });
    await screen.findByText("Engagement 1");
    fireEvent.change(screen.getByLabelText("Search displayed columns"), {
      target: { value: "acme" },
    });

    await screen.findByText("No rows match 'acme'.");
    expect(
      screen.getByText(
        "200 rows are in this view but hidden by your search or column filters — the data is still there.",
      ),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    await screen.findByText("Engagement 1");
    expect(screen.getByLabelText("Search displayed columns")).toHaveProperty(
      "value",
      "",
    );
  });

  it("offers retry on a data-source error, detail aside rather than dumped", async () => {
    let failures = 1;
    renderGrid({
      rows: () => {
        if (failures > 0) {
          failures -= 1;
          return refusal("storageUnavailable", "relation espo_rows is gone");
        }
        return ok({ rows: makeRows(1, 25), nextCursor: null });
      },
    });

    await screen.findByText("This grid couldn't load its data.");
    const state = element(".grid-state");
    // Plain words up front; the technical detail sits behind a disclosure.
    expect(state.textContent).toContain("The data source didn't answer correctly.");
    expect(element(".grid-state details pre").textContent).toContain(
      "storageUnavailable: relation espo_rows is gone",
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Engagement 1");
  });

  it("names the missing data-source permission and who grants it", async () => {
    renderGrid({
      rows: () => refusal("permissionMissing", "engagements.read"),
    });
    await screen.findByText("This grid can't be shown.");
    expect(
      screen.getByText(
        "Your account doesn't have the 'engagements.read' data-source permission this view reads from.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "Ask a system administrator to grant it — access is per data source.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });
});

// --- REQ-041/REQ-042: workprocess entries in the action lists -------------------

describe("workprocess actions (REQ-041, REQ-042)", () => {
  const REASSIGN = {
    workprocessRegistrationID: "wp-reassign",
    label: "Reassign Mentor",
    description: "Moves the selected engagement to another mentor.",
    selectionContract: "single",
    classification: "modifying",
  };
  const PURGE = {
    workprocessRegistrationID: "wp-purge",
    label: "Purge Engagements",
    description: "Removes the selected engagements.",
    selectionContract: "multiple",
    classification: "destructive",
  };

  function makeRun(over: Record<string, unknown>): unknown {
    return {
      workprocessRunID: "run-1",
      workprocessRegistrationID: "wp-reassign",
      runState: "inFlight",
      selectedRecordIDs: ["r1"],
      stepAnswers: {},
      currentStepKey: "chooseMentor",
      completable: false,
      rowVersion: 1,
      ...over,
    };
  }

  it("appends the data source's workprocesses to the one full menu, before Help", async () => {
    const { calls } = renderGrid({ workprocessActions: [REASSIGN, PURGE] });
    await screen.findByText("Engagement 1");

    // The list was read for the ACTIVE view's data source.
    expect(
      calls.some((url) => url.pathname === "/workprocesses/actions/engagements"),
    ).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Other Actions" }));
    const menu = screen.getByRole("list", { name: "All actions" });
    const labels = [...menu.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual([
      "Open",
      "Export",
      "Remove",
      "Reassign Mentor",
      "Purge Engagements",
      "Help",
    ]);
    // Never hidden, never disabled — REQ-041's whole point.
    expect(
      [...menu.querySelectorAll("button")].every((button) => !button.disabled),
    ).toBe(true);
  });

  it("explains a violating selection through the one invalid-invocation surface", async () => {
    renderGrid({ workprocessActions: [REASSIGN] });
    await screen.findByText("Engagement 1");

    // No selection against a single-record contract: the same educate words
    // the server's grid-standard explainer would give the same mistake.
    fireEvent.click(screen.getByRole("button", { name: "Other Actions" }));
    fireEvent.click(screen.getByRole("button", { name: "Reassign Mentor" }));
    const dialog = screen.getByRole("dialog", { name: "Why this didn't run" });
    expect(dialog.textContent).toContain("'Reassign Mentor' didn't run.");
    expect(dialog.textContent).toContain(
      "'Reassign Mentor' works on exactly one record, and no row is selected.",
    );
  });

  it("launches with the inherited selection, walks the steps, and refreshes on completion", async () => {
    const bodies: unknown[] = [];
    const { calls } = renderGrid({
      workprocessActions: [REASSIGN],
      workprocessRun: (url, init) => {
        // The surface always sends JSON string bodies (the envelope client's
        // replay contract) — anything else would be a defect worth failing on.
        bodies.push(typeof init?.body === "string" ? JSON.parse(init.body) : null);
        if (url.pathname === "/workprocesses/runs") {
          return ok(makeRun({}));
        }
        if (url.pathname.endsWith("/step")) {
          return ok(
            makeRun({
              stepAnswers: { chooseMentor: "mentor-9" },
              currentStepKey: null,
              completable: true,
            }),
          );
        }
        if (url.pathname.endsWith("/commit")) {
          return ok(
            makeRun({
              runState: "committed",
              currentStepKey: null,
              completable: false,
              confirmation: "'Reassign Mentor' completed and its changes were applied.",
            }),
          );
        }
        throw new Error(`Unrouted run verb: ${url.pathname}`);
      },
    });
    await screen.findByText("Engagement 1");
    fireEvent.click(screen.getByText("Engagement 1"));

    fireEvent.click(screen.getByRole("button", { name: "Other Actions" }));
    fireEvent.click(screen.getByRole("button", { name: "Reassign Mentor" }));

    // The launch inherited the selection and named the launching source.
    await screen.findByRole("dialog", { name: "Run 'Reassign Mentor'" });
    await screen.findByRole("heading", { name: "chooseMentor" });
    expect(bodies[0]).toEqual({
      workprocessRegistrationID: "wp-reassign",
      dataSourceKey: "engagements",
      selectedRecordIDs: ["r1"],
    });

    // The current step renders as the author's key with one free answer
    // control; the answer travels verbatim.
    fireEvent.change(screen.getByLabelText("Answer for step 'chooseMentor'"), {
      target: { value: "mentor-9" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByText(/Every step is answered/);
    expect(bodies[1]).toEqual({ stepKey: "chooseMentor", answer: "mentor-9" });

    // Nothing committed until completion; Complete applies and confirms.
    const rowReadsBefore = calls.filter((u) => u.pathname.includes("/rows")).length;
    fireEvent.click(screen.getByRole("button", { name: "Complete" }));
    await screen.findByText(
      "'Reassign Mentor' completed and its changes were applied.",
    );

    // The launching grid re-reads its rows in place after the commit.
    await waitFor(() => {
      expect(calls.filter((u) => u.pathname.includes("/rows")).length).toBe(
        rowReadsBefore + 1,
      );
    });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog", { name: "Run 'Reassign Mentor'" })).toBeNull();
  });

  it("confirms a destructive workprocess in the shared voice before any launch", async () => {
    const launches: string[] = [];
    renderGrid({
      workprocessActions: [PURGE],
      workprocessRun: (url) => {
        launches.push(url.pathname);
        return ok(makeRun({ workprocessRegistrationID: "wp-purge" }));
      },
    });
    await screen.findByText("Engagement 1");
    fireEvent.click(screen.getByText("Engagement 1"));
    fireEvent.click(screen.getByText("Engagement 2"), { ctrlKey: true });

    // Cancel drops the held launch — nothing was posted.
    fireEvent.click(screen.getByRole("button", { name: "Other Actions" }));
    fireEvent.click(screen.getByRole("button", { name: "Purge Engagements" }));
    let dialog = screen.getByRole("dialog", { name: "Purge Engagements 2 records?" });
    expect(dialog.textContent).toContain("an administrator can restore them");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(launches).toEqual([]);

    // Continue proceeds into the run.
    fireEvent.click(screen.getByRole("button", { name: "Other Actions" }));
    fireEvent.click(screen.getByRole("button", { name: "Purge Engagements" }));
    dialog = screen.getByRole("dialog", { name: "Purge Engagements 2 records?" });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("dialog", { name: "Run 'Purge Engagements'" });
    expect(launches).toEqual(["/workprocesses/runs"]);
  });
});
