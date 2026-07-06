/**
 * The universal grid panel (WTK-048, REQ-016, REQ-020..REQ-031): the React
 * rendering of `ui/grid_panel.py`'s design — three stacked regions (action
 * bar / data table / status bar), the view selector with its modified flag,
 * live search with recent-search history, the never-hide action machinery,
 * entire-filtered-set selection with keep-with-notice, multi-column header
 * sorting, the one keyboard model, infinite scroll, and the four
 * distinguished grid states. Server copy renders verbatim; interaction
 * behavior mirrors the Python model via ./model.ts.
 */

import {
  type KeyboardEvent,
  type ReactElement,
  type UIEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { callApi, EnvelopeError } from "../api/envelope";
import { useEnvelope } from "../api/useEnvelope";
import { DeclinedNotice, EducateNotice, UnreachableNotice } from "../shell/educate";
import { PanelSplitter, ResizablePanel, usePanelChrome } from "../shell/panel-chrome";
import { readSession } from "../session";
import { RecordPreview } from "../windows/record";
import {
  actionMenus,
  bindingFor,
  destructiveConfirmation,
  EMPTY_SELECTION,
  invalidInvocation,
  isModified,
  markModified,
  MIN_SEARCH_LENGTH,
  multiSelectActive,
  resolveGridState,
  rememberSearch,
  rowCountLabel,
  searchIsLive,
  selectedCount,
  selectView,
  type Selection,
  sortBadgeFor,
  sortClick,
  sortShiftClick,
  type SortKey,
  toggleSelected,
  type ViewSelectorState,
  type ActionConfirmation,
} from "./model";
import {
  type ActionPayload,
  type GridAggregatesPayload,
  type GridPanelPayload,
  type GridRowPayload,
  type GridRowsPayload,
} from "./payloads";
import { type EducatePayload } from "../api/payloads";

/** How close to the bottom edge (rows) the scroll gets before the next page loads. */
const LOAD_AHEAD_ROWS = 10;

function rowsPath(
  gridId: string,
  view: ViewSelectorState,
  searchText: string,
  sortKeys: readonly SortKey[],
  cursor: string | null,
): string {
  const query = new URLSearchParams({ view: view.activeViewKey });
  if (searchIsLive(searchText)) {
    query.set("search", searchText.trim());
  }
  if (sortKeys.length > 0) {
    query.set(
      "sort",
      sortKeys.map((k) => `${k.fieldName}:${k.descending ? "desc" : "asc"}`).join(","),
    );
  }
  if (cursor !== null) {
    query.set("cursor", cursor);
  }
  return `/grids/${encodeURIComponent(gridId)}/rows?${query.toString()}`;
}

/** Fetch-state for the row window; aggregates arrive independently (REQ-026). */
interface RowsState {
  rows: GridRowPayload[];
  nextCursor: string | null;
  loading: boolean;
  loadError: string | null;
  permissionMissing: string | null;
}

export function GridPanel({ panelKey }: { panelKey: string }): ReactElement {
  const { state, reload } = useEnvelope<GridPanelPayload>(
    `/panels/${encodeURIComponent(panelKey)}/grid`,
  );

  if (state.phase === "loading") {
    return <p role="status">Loading grid</p>;
  }
  if (state.phase === "declined") {
    return <DeclinedNotice errors={state.errors} />;
  }
  if (state.phase === "unreachable") {
    return <UnreachableNotice />;
  }
  return (
    <LoadedGrid key={state.data.gridId} panel={state.data} onReloadPanel={reload} />
  );
}

function LoadedGrid({
  panel,
  onReloadPanel,
}: {
  panel: GridPanelPayload;
  onReloadPanel: () => void;
}): ReactElement {
  const [view, setView] = useState<ViewSelectorState>(() =>
    selectView(panel.activeViewKey),
  );
  // Preview-pane size + zoom persist per user (REQ-087 via panel chrome).
  const panelChrome = usePanelChrome(readSession());
  const [searchText, setSearchText] = useState("");
  const [recentSearches, setRecentSearches] = useState(panel.recentSearches);
  const [sortKeys, setSortKeys] = useState<readonly SortKey[]>([]);
  const [selection, setSelection] = useState<Selection>(EMPTY_SELECTION);
  const [focusedRow, setFocusedRow] = useState(0);
  const [rowsState, setRowsState] = useState<RowsState>({
    rows: [],
    nextCursor: null,
    loading: true,
    loadError: null,
    permissionMissing: null,
  });
  const [aggregates, setAggregates] = useState<GridAggregatesPayload | null>(null);
  const [menuOpen, setMenuOpen] = useState<false | "dropdown" | "context">(false);
  const [explainer, setExplainer] = useState<EducatePayload | null>(null);
  const [confirmation, setConfirmation] = useState<ActionConfirmation | null>(null);
  const [showErrorDetail, setShowErrorDetail] = useState(false);
  const [generation, setGeneration] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);
  const activeView = panel.views.find((v) => v.viewKey === view.activeViewKey);

  // First page + aggregates load in parallel; both re-run when the query
  // (view / armed search / sort) changes. Aggregates never wait for rows.
  useEffect(() => {
    let cancelled = false;
    setRowsState((prev) => ({ ...prev, loading: true, loadError: null }));
    const query = rowsPath(panel.gridId, view, searchText, sortKeys, null);
    void callApi<GridRowsPayload>(query)
      .then((result) => {
        if (!cancelled) {
          setRowsState({
            rows: result.data.rows,
            nextCursor: result.data.nextCursor,
            loading: false,
            loadError: null,
            permissionMissing: null,
          });
        }
      })
      .catch((failure: unknown) => {
        if (cancelled) {
          return;
        }
        const denied =
          failure instanceof EnvelopeError
            ? failure.errors.find((e) => e.code === "permissionMissing")
            : undefined;
        setRowsState({
          rows: [],
          nextCursor: null,
          loading: false,
          loadError: denied ? null : describeFailure(failure),
          permissionMissing: denied ? denied.message : null,
        });
      });
    void callApi<GridAggregatesPayload>(
      `/grids/${encodeURIComponent(panel.gridId)}/aggregates?view=${encodeURIComponent(view.activeViewKey)}` +
        (searchIsLive(searchText)
          ? `&search=${encodeURIComponent(searchText.trim())}`
          : ""),
    )
      .then((result) => {
        if (!cancelled) {
          setAggregates(result.data);
        }
      })
      .catch(() => {
        // The rows fetch owns error rendering; a lost count fills in on retry.
        if (!cancelled) {
          setAggregates(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [panel.gridId, view, searchText, sortKeys, generation]);

  const loadNextPage = useCallback(() => {
    if (rowsState.nextCursor === null || rowsState.loading) {
      return;
    }
    const cursor = rowsState.nextCursor;
    setRowsState((prev) => ({ ...prev, loading: true }));
    void callApi<GridRowsPayload>(
      rowsPath(panel.gridId, view, searchText, sortKeys, cursor),
    )
      .then((result) => {
        setRowsState((prev) => ({
          ...prev,
          rows: [...prev.rows, ...result.data.rows],
          nextCursor: result.data.nextCursor,
          loading: false,
        }));
      })
      .catch((failure: unknown) => {
        setRowsState((prev) => ({
          ...prev,
          loading: false,
          loadError: describeFailure(failure),
        }));
      });
  }, [
    panel.gridId,
    view,
    searchText,
    sortKeys,
    rowsState.nextCursor,
    rowsState.loading,
  ]);

  const totalCount = aggregates?.totalCount ?? rowsState.rows.length;
  const selCount = selectedCount(selection, totalCount);
  // Filter/search changes keep the selection, with notice: explicit ids not
  // in the loaded-or-counted set are the "not in current filter" rows.
  const hiddenSelected =
    selection.kind === "explicit"
      ? selection.recordIds.filter(
          (id) => !rowsState.rows.some((row) => row.recordId === id),
        ).length
      : 0;

  const menus = actionMenus(panel.actions, panel.commonActionKeys);

  const runAction = (action: ActionPayload): void => {
    setMenuOpen(false);
    const refusal = invalidInvocation(action, selCount);
    if (refusal !== null) {
      setExplainer(refusal);
      return;
    }
    if (action.classification === "destructive") {
      const titles =
        selection.kind === "explicit"
          ? selection.recordIds.map(
              (id) => rowsState.rows.find((row) => row.recordId === id)?.title ?? id,
            )
          : rowsState.rows.map((row) => row.title);
      setConfirmation(destructiveConfirmation(action, titles, hiddenSelected));
      return;
    }
    // Safe/modifying invocation wiring rides the records router's action
    // surface; the grid's contract ends at a valid, confirmed invocation.
  };

  const sortHeader = (fieldName: string, extend: boolean): void => {
    // The one place sorting couples to the modified flag (REQ-025/REQ-031).
    setSortKeys((keys) =>
      extend ? sortShiftClick(keys, fieldName) : sortClick(keys, fieldName),
    );
    setView((v) => markModified(v, "sort"));
  };

  const gridKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    // Browsers report Ctrl-chorded letters lowercase ("a", never "A"); the
    // keyboard model speaks "Ctrl+A", so chorded single letters normalize up.
    const namedKey = event.key === " " ? "Space" : event.key;
    const key =
      (event.ctrlKey ? "Ctrl+" : "") +
      (event.shiftKey && event.key !== "Shift" ? "Shift+" : "") +
      (event.ctrlKey && namedKey.length === 1 ? namedKey.toUpperCase() : namedKey);
    const action = bindingFor(key, "rows");
    if (action === null) {
      return;
    }
    event.preventDefault();
    if (action === "moveRowFocus" || action === "extendSelection") {
      const delta = event.key === "ArrowUp" ? -1 : 1;
      const next = Math.max(0, Math.min(rowsState.rows.length - 1, focusedRow + delta));
      setFocusedRow(next);
      const nextRow = rowsState.rows[next];
      if (action === "extendSelection" && nextRow !== undefined) {
        setSelection((sel) => toggleSelected(sel, nextRow.recordId));
      }
      // Arrows never dead-end against the infinite scroll (REQ-024).
      if (next >= rowsState.rows.length - LOAD_AHEAD_ROWS) {
        loadNextPage();
      }
    } else if (action === "toggleSelection") {
      const row = rowsState.rows[focusedRow];
      if (row !== undefined) {
        setSelection((sel) => toggleSelected(sel, row.recordId));
      }
    } else if (action === "selectEntireFilteredSet") {
      setSelection({ kind: "filteredSet" });
    } else if (action === "openFocusedRecord") {
      openRecord(rowsState.rows[focusedRow]);
    } else if (action === "openActionsMenu") {
      setMenuOpen("context");
    } else if (action === "focusSearchBox") {
      searchRef.current?.focus();
    }
  };

  const openRecord = (row: GridRowPayload | undefined): void => {
    if (row === undefined) {
      return;
    }
    // Double-click/Enter = open (REQ-023); records open as pop-out-capable
    // real windows per the layout standard's window model.
    window.open(`/records/grid/${encodeURIComponent(row.recordId)}`, "_blank");
  };

  const gridState = resolveGridState({
    viewLabel: activeView?.label ?? view.activeViewKey,
    viewCriteria: activeView?.criteria ?? "its saved criteria",
    filteredCount: rowsState.loading && rowsState.rows.length === 0 ? 1 : totalCount,
    unnarrowedCount: aggregates?.unnarrowedCount ?? 0,
    searchText,
    adHocFilterCount: 0,
    loadError: rowsState.loadError,
    permissionMissing: rowsState.permissionMissing,
  });

  // The docked read-optimized preview follows the selection (REQ-012,
  // WTK-229 rework): one selected row previews it; otherwise the focused
  // row. Sizing/zoom persist through the shared panel chrome (REQ-087);
  // fill-to-space content lands with the domain rollup (REQ-088, PI-010).
  const previewRecordId =
    selection.kind === "explicit" && selection.recordIds.length === 1
      ? selection.recordIds[0]
      : rowsState.rows[focusedRow]?.recordId;

  return (
    <div className="grid-with-preview">
      <section
        className="grid-panel"
        aria-label={panel.title}
        onContextMenu={(event) => {
          event.preventDefault();
          setMenuOpen("context");
        }}
      >
        {/* Region 1: the action bar — view machinery / search / actions. */}
        <div className="grid-action-bar" role="toolbar" aria-label="Grid actions">
          <span>
            <select
              aria-label="View"
              value={view.activeViewKey}
              onChange={(event) => {
                setView(selectView(event.target.value));
                setSortKeys([]);
              }}
            >
              {panel.views.map((v) => (
                <option key={v.viewKey} value={v.viewKey}>
                  {v.label}
                  {v.viewKey === view.activeViewKey && isModified(view)
                    ? " (modified)"
                    : ""}
                </option>
              ))}
            </select>
            {isModified(view) ? (
              <button type="button" title="Save these settings as your own view">
                Save as my view
              </button>
            ) : null}
            <button type="button" aria-label="Edit view settings">
              ⚙
            </button>
          </span>
          <span>
            <input
              ref={searchRef}
              type="search"
              autoFocus
              aria-label="Search displayed columns"
              placeholder={`Search (from ${String(MIN_SEARCH_LENGTH)} characters)`}
              list={`recent-searches-${panel.gridId}`}
              value={searchText}
              onChange={(event) => {
                setSearchText(event.target.value);
                if (searchIsLive(event.target.value)) {
                  setRecentSearches((prev) => rememberSearch(prev, event.target.value));
                }
              }}
            />
            <datalist id={`recent-searches-${panel.gridId}`}>
              {recentSearches.map((entry) => (
                <option key={entry} value={entry} />
              ))}
            </datalist>
          </span>
          <span>
            {menus.buttons.map((action) => (
              <button
                key={action.key}
                type="button"
                onClick={() => {
                  runAction(action);
                }}
              >
                {action.label}
              </button>
            ))}
            <button
              type="button"
              onClick={() => {
                setMenuOpen("dropdown");
              }}
            >
              Other Actions
            </button>
          </span>
        </div>

        {menuOpen !== false ? (
          <menu aria-label="All actions">
            {menus.menu.map((action) => (
              <li key={action.key}>
                <button
                  type="button"
                  onClick={() => {
                    runAction(action);
                  }}
                >
                  {action.label}
                </button>
              </li>
            ))}
          </menu>
        ) : null}

        {explainer !== null ? (
          <dialog open aria-label="Why this didn't run">
            <EducateNotice notice={explainer} />
            <button
              type="button"
              onClick={() => {
                setExplainer(null);
              }}
            >
              OK
            </button>
          </dialog>
        ) : null}

        {confirmation !== null ? (
          <dialog open aria-label={confirmation.title}>
            <p>{confirmation.title}</p>
            <ul>
              {confirmation.listedTitles.map((title) => (
                <li key={title}>{title}</li>
              ))}
            </ul>
            {confirmation.moreCount > 0 ? (
              <p>…and {confirmation.moreCount} more</p>
            ) : null}
            {confirmation.hiddenRowsNotice !== null ? (
              <p>{confirmation.hiddenRowsNotice}</p>
            ) : null}
            {confirmation.honestyNote !== null ? (
              <p>{confirmation.honestyNote}</p>
            ) : null}
            <button
              type="button"
              onClick={() => {
                setConfirmation(null);
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirmation(null);
              }}
            >
              Continue
            </button>
          </dialog>
        ) : null}

        {/* Region 2: the data table, or the one distinguished state. */}
        {gridState !== null ? (
          <div className="grid-state" role="status">
            <EducateNotice notice={gridState.message} />
            {gridState.affordances.includes("retry") ? (
              <button
                type="button"
                onClick={() => {
                  setGeneration((g) => g + 1);
                  onReloadPanel();
                }}
              >
                Retry
              </button>
            ) : null}
            {gridState.affordances.includes("clearSearch") &&
            searchIsLive(searchText) ? (
              <button
                type="button"
                onClick={() => {
                  setSearchText("");
                }}
              >
                Clear search
              </button>
            ) : null}
            {gridState.affordances.includes("showDetail") &&
            gridState.detail !== null ? (
              <details
                onToggle={() => {
                  setShowErrorDetail(!showErrorDetail);
                }}
              >
                <summary>Technical detail</summary>
                <pre>{gridState.detail}</pre>
              </details>
            ) : null}
          </div>
        ) : (
          <div
            className="grid-table-host"
            tabIndex={0}
            onKeyDown={gridKeyDown}
            onScroll={(event: UIEvent<HTMLDivElement>) => {
              const el = event.currentTarget;
              if (el.scrollTop + el.clientHeight >= el.scrollHeight - el.clientHeight) {
                loadNextPage();
              }
            }}
          >
            <table className="grid-table">
              <thead>
                <tr>
                  {multiSelectActive(selection) ? <th aria-label="Selected" /> : null}
                  {panel.columns.map((column) => {
                    const badge = sortBadgeFor(sortKeys, column.fieldName);
                    return (
                      <th
                        key={column.fieldName}
                        tabIndex={0}
                        aria-sort={
                          badge === null
                            ? undefined
                            : badge.direction === "asc"
                              ? "ascending"
                              : "descending"
                        }
                        onClick={(event) => {
                          sortHeader(column.fieldName, event.shiftKey);
                        }}
                        onKeyDown={(event) => {
                          if (bindingFor(event.key, "columnHeader") === "sortColumn") {
                            event.preventDefault();
                            sortHeader(column.fieldName, event.shiftKey);
                          }
                        }}
                      >
                        {column.label}
                        {badge !== null ? (
                          <span className="sort-badge">
                            {badge.direction === "asc" ? "▲" : "▼"}
                            {badge.position}
                          </span>
                        ) : null}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {rowsState.rows.map((row, index) => {
                  const isSelected =
                    selection.kind === "filteredSet" ||
                    selection.recordIds.includes(row.recordId);
                  return (
                    <tr
                      key={row.recordId}
                      aria-selected={isSelected}
                      className={index === focusedRow ? "grid-row-focused" : undefined}
                      onClick={(event) => {
                        setFocusedRow(index);
                        if (event.shiftKey || event.ctrlKey) {
                          setSelection((sel) => toggleSelected(sel, row.recordId));
                        } else {
                          setSelection({ kind: "explicit", recordIds: [row.recordId] });
                        }
                      }}
                      onDoubleClick={() => {
                        openRecord(row);
                      }}
                    >
                      {multiSelectActive(selection) ? (
                        <td>
                          <input
                            type="checkbox"
                            aria-label={`Select ${row.title}`}
                            checked={isSelected}
                            onChange={() => {
                              setSelection((sel) => toggleSelected(sel, row.recordId));
                            }}
                          />
                        </td>
                      ) : null}
                      {panel.columns.map((column) => (
                        <td key={column.fieldName}>
                          {row.values[column.fieldName] ?? ""}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
              {aggregates !== null && Object.keys(aggregates.footer).length > 0 ? (
                <tfoot>
                  <tr>
                    {multiSelectActive(selection) ? <td /> : null}
                    {panel.columns.map((column) => (
                      <td key={column.fieldName}>
                        {aggregates.footer[column.fieldName] ?? ""}
                      </td>
                    ))}
                  </tr>
                </tfoot>
              ) : null}
            </table>
          </div>
        )}

        {/* Region 3: the status bar — progress middle, whole-set count right. */}
        <div className="grid-status-bar">
          <span role="status">{rowsState.loading ? "Loading grid" : ""}</span>
          <span>{rowCountLabel(totalCount, selCount, hiddenSelected)}</span>
        </div>
      </section>
      <PanelSplitter
        panelKey={`gridPreview:${panel.gridId}`}
        onResize={panelChrome.saveWidth}
        resizes="next"
        maxWidth={720}
      />
      <ResizablePanel
        panelKey={`gridPreview:${panel.gridId}`}
        savedWidth={panelChrome.document.widths[`gridPreview:${panel.gridId}`] ?? 380}
        zoom={panelChrome.document.zooms[`gridPreview:${panel.gridId}`] ?? 100}
        onZoom={panelChrome.saveZoom}
        className="grid-preview-panel"
      >
        {previewRecordId === undefined ? (
          <p className="grid-preview-hint">
            Select a row to preview it here — the preview always follows the selected
            row. Read-optimized: editing is the Edit action or a field's double-click,
            never this pane.
          </p>
        ) : (
          <RecordPreview entityType="grid" recordId={previewRecordId} />
        )}
      </ResizablePanel>
    </div>
  );
}

function describeFailure(failure: unknown): string {
  if (failure instanceof EnvelopeError) {
    return failure.errors.map((error) => `${error.code}: ${error.message}`).join("; ");
  }
  return "The CBM Mentoring API did not answer.";
}
