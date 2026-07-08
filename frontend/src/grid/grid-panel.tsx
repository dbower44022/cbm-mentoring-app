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
  type CSSProperties,
  type KeyboardEvent,
  type ReactElement,
  type UIEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { callApi, EnvelopeError } from "../api/envelope";
import { useEnvelope } from "../api/useEnvelope";
import { DeclinedNotice, EducateNotice, UnreachableNotice } from "../shell/educate";
import { openHelp } from "../shell/help";
import { PanelSplitter, ResizablePanel, usePanelChrome } from "../shell/panel-chrome";
import { readSession } from "../session";
import { popOutRecordEdit, RecordPreview } from "../windows/record";
import { popOutRecordCreate } from "../windows/record-create";
import {
  actionMenus,
  bindingFor,
  destructiveConfirmation,
  EDIT_RECORD_ACTION,
  EMPTY_SELECTION,
  HELP_ACTION,
  NEW_RECORD_ACTION,
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
import { formatCell } from "./format";
import { rowEffects } from "./formatting";
import {
  lifecycleTransitionFor,
  mentoringPanelActions,
  SEND_EMAIL_ACTION,
  SHARE_RESOURCE_ACTION,
} from "../mentoring/actions";
import { ComposeEmailDialog } from "../mentoring/compose-email";
import { LifecycleDialog } from "../mentoring/lifecycle-dialog";
import { ShareResourceDialog } from "../mentoring/share-resource";
import { previewRendererFor } from "./preview-seam";
import { launchSelection, workprocessPanelAction } from "../workprocess/model";
import { type WorkprocessActionPayload } from "../workprocess/payloads";
import { WorkprocessRunPanel } from "../workprocess/run-panel";

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
  // The panel surface (WTK-233): gridId IS the panel key, and the /panels
  // endpoints serve the seeded area sources — views are addressed by their
  // stable view key, matching the panel payload this component booted from.
  return `/panels/${encodeURIComponent(gridId)}/rows?${query.toString()}`;
}

/** Fetch-state for the row window; aggregates arrive independently (REQ-026). */
interface RowsState {
  rows: GridRowPayload[];
  nextCursor: string | null;
  loading: boolean;
  loadError: string | null;
  permissionMissing: string | null;
}

/** REQ-107: a column's minimum width in characters — the label must always
    fit, with a floor for narrow labels so data stays legible. */
function minChars(column: { label: string }): number {
  return Math.max(6, Math.min(24, column.label.length + 2));
}

/** One character's width in px, measured in the header's own font — the
    ch->px conversion the drag clamp needs (min-width:ch is ignored once
    the table switches to fixed layout for user-resized columns). */
function chWidthPx(host: HTMLElement): number {
  const probe = document.createElement("span");
  probe.textContent = "0";
  probe.style.visibility = "hidden";
  probe.style.position = "absolute";
  host.appendChild(probe);
  const width = probe.getBoundingClientRect().width;
  probe.remove();
  return width > 0 ? width : 8;
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
    <LoadedGrid
      key={state.data.gridId}
      panel={state.data}
      panelKey={panelKey}
      onReloadPanel={reload}
    />
  );
}

function LoadedGrid({
  panel,
  panelKey,
  onReloadPanel,
}: {
  panel: GridPanelPayload;
  panelKey: string;
  onReloadPanel: () => void;
}): ReactElement {
  const [view, setView] = useState<ViewSelectorState>(() =>
    selectView(panel.activeViewKey),
  );
  // Preview-pane size + zoom persist per user (REQ-087 via panel chrome).
  // Stable identity: readSession() mints a new object per call (see
  // panel-chrome's sessionKey note).
  const gridSession = useMemo(() => readSession(), []);
  const panelChrome = usePanelChrome(gridSession);
  const [searchText, setSearchText] = useState("");
  const [recentSearches, setRecentSearches] = useState(panel.recentSearches);
  const [sortKeys, setSortKeys] = useState<readonly SortKey[]>([]);
  // REQ-107: user-resized column widths (px), session-scoped like sort —
  // a temporary view modification. The CHARACTER minimum is enforced in CSS
  // (min-width in ch on every header/cell), so neither drags nor auto
  // sizing can squash a column below it.
  const [colWidths, setColWidths] = useState<Record<string, number>>({});
  // A resize drag must never read as a header click (Doug's REQ-107
  // clarification): the flag lives through the drag AND the click event
  // that fires after mouseup, then clears on the next tick.
  const resizingColumn = useRef(false);
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
  // "dropdown" anchors under the Other Actions button; a coordinate pair is
  // a right-click, anchored AT the cursor (REQ-106: zero mouse travel).
  const [menuOpen, setMenuOpen] = useState<
    false | "dropdown" | { x: number; y: number }
  >(false);
  const [explainer, setExplainer] = useState<EducatePayload | null>(null);
  // The educate notice a generic help landing carries (REQ-043) — the
  // grid's own dismissible surface for the shell resolver's notice sink.
  const [helpNotice, setHelpNotice] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<ActionConfirmation | null>(null);
  const [showErrorDetail, setShowErrorDetail] = useState(false);
  const [generation, setGeneration] = useState(0);
  // The data source's registered workprocesses (REQ-041), joined into the
  // same action menus as the grid's own actions.
  const [workprocessEntries, setWorkprocessEntries] = useState<
    WorkprocessActionPayload[]
  >([]);
  // A destructive workprocess held behind the shared confirmation dialog:
  // Continue launches it; Cancel drops it (REQ-042's confirm-before-launch).
  const [pendingLaunch, setPendingLaunch] = useState<WorkprocessActionPayload | null>(
    null,
  );
  const [activeRun, setActiveRun] = useState<{
    entry: WorkprocessActionPayload;
    dataSourceKey: string;
    selectedRecordIds: string[];
  } | null>(null);
  // The mentoring flows an engagement/resource grid launches (WTK-183/169/
  // 178): a lifecycle transition held in its confirm dialog, the templated
  // email compose, the share-a-resource compose.
  const [lifecycleFlow, setLifecycleFlow] = useState<{
    action: ActionPayload;
    transition: string;
    engagementId: string;
  } | null>(null);
  const [composeFor, setComposeFor] = useState<string | null>(null);
  const [shareFor, setShareFor] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const activeView = panel.views.find((v) => v.viewKey === view.activeViewKey);
  const dataSourceKey = activeView?.dataSourceKey;

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
      `/panels/${encodeURIComponent(panel.gridId)}/aggregates?view=${encodeURIComponent(view.activeViewKey)}` +
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

  // The active view's data source names its workprocess action list
  // (REQ-041). The read is additive: a failed fetch must not degrade the
  // grid — the built-in actions still serve — and an uncovered source
  // already answers an empty list, indistinguishable from an unknown one.
  useEffect(() => {
    if (dataSourceKey === undefined) {
      setWorkprocessEntries([]);
      return;
    }
    let cancelled = false;
    void callApi<WorkprocessActionPayload[]>(
      `/workprocesses/actions/${encodeURIComponent(dataSourceKey)}`,
    )
      .then((result) => {
        if (!cancelled) {
          setWorkprocessEntries(result.data);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setWorkprocessEntries([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [dataSourceKey]);

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

  // Mentoring domain actions join exactly as workprocess entries do — the
  // one action-menu fold, keyed by the active view's data source (WTK-183).
  const menus = actionMenus(panel.actions, panel.commonActionKeys, [
    // Edit and New join every grid whose active view names an app entity
    // (REQ-032/037); a projected source (entityType null) has no record
    // to edit and no data set to create into.
    ...(activeView?.entityType != null ? [EDIT_RECORD_ACTION, NEW_RECORD_ACTION] : []),
    ...workprocessEntries.map(workprocessPanelAction),
    ...mentoringPanelActions(dataSourceKey),
  ]);

  const openWorkprocess = (entry: WorkprocessActionPayload): void => {
    if (dataSourceKey === undefined) {
      return; // No live view — there was no action list to invoke from.
    }
    setActiveRun({
      entry,
      dataSourceKey,
      // The run inherits the selection AT LAUNCH (REQ-042): captured here,
      // not read live, so grid interaction behind the dialog can't move it.
      selectedRecordIds: launchSelection(selection, rowsState.rows),
    });
  };

  const runAction = (action: ActionPayload): void => {
    setMenuOpen(false);
    if (action.key === HELP_ACTION.key) {
      // Every menu's last item is Help (REQ-043), resolved through the ONE
      // path (SKL-122). The grid's help identity is its CONTENT: the active
      // view's data set when one is live — that's what the user is looking
      // at — falling back to the hosting panel before a view resolves.
      void openHelp(
        dataSourceKey !== undefined ? "dataSet" : "panel",
        dataSourceKey ?? panelKey,
        setHelpNotice,
      );
      return;
    }
    // One explainer for every entry, workprocess or built-in (REQ-041): the
    // model mirrors the server's grid-standard invalid_invocation verbatim,
    // so a violating selection gets the server's own educate words here.
    const refusal = invalidInvocation(action, selCount);
    if (refusal !== null) {
      setExplainer(refusal);
      return;
    }
    // The mentoring flows (WTK-183/169/178): all contract "single", already
    // validated above, so the one selected/focused row is the subject.
    const [subjectId] = launchSelection(selection, rowsState.rows);
    if (action.key === NEW_RECORD_ACTION.key && activeView?.entityType != null) {
      // The same form, empty, in a fresh window (REQ-037).
      popOutRecordCreate(activeView.entityType);
      return;
    }
    if (
      action.key === EDIT_RECORD_ACTION.key &&
      subjectId !== undefined &&
      activeView?.entityType != null
    ) {
      // The full-screen form opens in the record's pinned pop-out window
      // (one window per record); the grid keeps working behind it.
      popOutRecordEdit(activeView.entityType, subjectId);
      return;
    }
    const transition = lifecycleTransitionFor(action.key);
    if (transition !== null && subjectId !== undefined) {
      // Modifying classification: the transition confirms in its own dialog
      // before the POST (DEC-071 — decline is a status change, not a
      // destructive act, so it never takes the destructive path).
      setLifecycleFlow({ action, transition, engagementId: subjectId });
      return;
    }
    if (action.key === SEND_EMAIL_ACTION.key && subjectId !== undefined) {
      setComposeFor(subjectId);
      return;
    }
    if (action.key === SHARE_RESOURCE_ACTION.key && subjectId !== undefined) {
      setShareFor(subjectId);
      return;
    }
    const workprocess = workprocessEntries.find(
      (entry) => entry.workprocessRegistrationID === action.key,
    );
    if (action.classification === "destructive") {
      const titles =
        selection.kind === "explicit"
          ? selection.recordIds.map(
              (id) => rowsState.rows.find((row) => row.recordId === id)?.title ?? id,
            )
          : rowsState.rows.map((row) => row.title);
      setConfirmation(destructiveConfirmation(action, titles, hiddenSelected));
      // A destructive workprocess launches only after the SAME confirmation
      // voice a destructive grid action gets (REQ-042): held here until
      // Continue; built-in actions keep their confirm-only contract below.
      setPendingLaunch(workprocess ?? null);
      return;
    }
    if (workprocess !== undefined) {
      openWorkprocess(workprocess);
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
      // Keyboard invocation has no cursor; anchor like the button.
      setMenuOpen("dropdown");
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
  const DomainPreview = previewRendererFor(dataSourceKey);

  return (
    <div className="grid-with-preview">
      <section
        className="grid-panel"
        aria-label={panel.title}
        onContextMenu={(event) => {
          event.preventDefault();
          setMenuOpen({ x: event.clientX, y: event.clientY });
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
          <>
            {/* Transparent backdrop: outside click closes the menu without
                stealing the click's meaning from grid rows (REQ-021 menu UX). */}
            <div
              className="grid-menu-backdrop"
              onClick={() => {
                setMenuOpen(false);
              }}
            />
            <menu
              aria-label="All actions"
              style={
                menuOpen === "dropdown"
                  ? undefined
                  : {
                      position: "fixed",
                      left: Math.min(menuOpen.x, window.innerWidth - 260),
                      top: Math.min(menuOpen.y, window.innerHeight - 320),
                      right: "auto",
                    }
              }
            >
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
          </>
        ) : null}

        {helpNotice !== null ? (
          <p className="notice" role="status">
            {helpNotice}{" "}
            <button
              type="button"
              onClick={() => {
                setHelpNotice(null);
              }}
            >
              Dismiss
            </button>
          </p>
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
                setPendingLaunch(null);
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirmation(null);
                if (pendingLaunch !== null) {
                  openWorkprocess(pendingLaunch);
                  setPendingLaunch(null);
                }
              }}
            >
              Continue
            </button>
          </dialog>
        ) : null}

        {lifecycleFlow !== null ? (
          <LifecycleDialog
            action={lifecycleFlow.action}
            transition={lifecycleFlow.transition}
            engagementId={lifecycleFlow.engagementId}
            onCompleted={() => {
              // The status flip is real the moment it lands — rows and
              // aggregates re-read in place (the workprocess-commit rule).
              setGeneration((g) => g + 1);
            }}
            onClose={() => {
              setLifecycleFlow(null);
            }}
          />
        ) : null}

        {composeFor !== null ? (
          <ComposeEmailDialog
            engagementId={composeFor}
            onClose={() => {
              setComposeFor(null);
            }}
          />
        ) : null}

        {shareFor !== null ? (
          <ShareResourceDialog
            resourceId={shareFor}
            onClose={() => {
              setShareFor(null);
            }}
          />
        ) : null}

        {activeRun !== null ? (
          <WorkprocessRunPanel
            entry={activeRun.entry}
            dataSourceKey={activeRun.dataSourceKey}
            selectedRecordIds={activeRun.selectedRecordIds}
            onCompleted={() => {
              // Completion re-reads rows + aggregates in place (REQ-042's
              // "the launching grid refreshes"). A direct callback suffices
              // because the run is an overlay of THIS panel — the
              // BroadcastChannel pattern (session.ts) is only needed when a
              // surface lives in a separate window.
              setGeneration((g) => g + 1);
            }}
            onClose={() => {
              setActiveRun(null);
            }}
          />
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
            <table
              className="grid-table"
              style={
                Object.keys(colWidths).length > 0 ? { tableLayout: "fixed" } : undefined
              }
            >
              <thead>
                <tr>
                  {multiSelectActive(selection) ? <th aria-label="Selected" /> : null}
                  {panel.columns.map((column) => {
                    const badge = sortBadgeFor(sortKeys, column.fieldName);
                    return (
                      <th
                        key={column.fieldName}
                        data-field={column.fieldName}
                        tabIndex={0}
                        style={{
                          textAlign: column.alignment,
                          minWidth: `${String(minChars(column))}ch`,
                          width:
                            colWidths[column.fieldName] === undefined
                              ? undefined
                              : `${String(colWidths[column.fieldName])}px`,
                        }}
                        aria-sort={
                          badge === null
                            ? undefined
                            : badge.direction === "asc"
                              ? "ascending"
                              : "descending"
                        }
                        onClick={(event) => {
                          if (resizingColumn.current) {
                            return;
                          }
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
                        {/* REQ-107: drag the header boundary to resize. */}
                        <span
                          className="col-resize-handle"
                          role="separator"
                          aria-orientation="vertical"
                          aria-label={`Resize ${column.label}`}
                          onClick={(event) => {
                            event.stopPropagation();
                          }}
                          onMouseDown={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            const th = event.currentTarget.closest("th");
                            const headerRow = th?.parentElement;
                            if (
                              th === null ||
                              headerRow === null ||
                              headerRow === undefined
                            ) {
                              return;
                            }
                            // First resize: snapshot every column's current
                            // width so the table can switch to fixed layout
                            // (auto layout re-distributes and ignores a
                            // single column's width) without a visual jump.
                            setColWidths((widths) => {
                              if (Object.keys(widths).length > 0) {
                                return widths;
                              }
                              const snapshot: Record<string, number> = {};
                              headerRow
                                .querySelectorAll("th[data-field]")
                                .forEach((cell) => {
                                  const field = cell.getAttribute("data-field");
                                  if (field !== null) {
                                    snapshot[field] =
                                      cell.getBoundingClientRect().width;
                                  }
                                });
                              return snapshot;
                            });
                            const startX = event.clientX;
                            const startW = th.getBoundingClientRect().width;
                            const minPx = minChars(column) * chWidthPx(th);
                            const move = (ev: MouseEvent): void => {
                              setColWidths((widths) => ({
                                ...widths,
                                [column.fieldName]: Math.max(
                                  minPx,
                                  startW + (ev.clientX - startX),
                                ),
                              }));
                            };
                            resizingColumn.current = true;
                            const up = (): void => {
                              document.removeEventListener("mousemove", move);
                              document.removeEventListener("mouseup", up);
                              // The header's click fires after mouseup —
                              // clear the flag once that tick has passed.
                              setTimeout(() => {
                                resizingColumn.current = false;
                              }, 0);
                            };
                            document.addEventListener("mousemove", move);
                            document.addEventListener("mouseup", up);
                          }}
                        />
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
                  // The view's REQ-045 rules, first-match-wins per target
                  // (FND-909 D7): row effects paint the whole row through
                  // the theme's status-slot variables; the accent effect
                  // paints its condition field's cell as a chip below.
                  const effects = rowEffects(panel.formattingRules, row.values);
                  return (
                    <tr
                      key={row.recordId}
                      aria-selected={isSelected}
                      className={index === focusedRow ? "grid-row-focused" : undefined}
                      style={
                        effects.rowBackground === null && effects.rowText === null
                          ? undefined
                          : {
                              background: effects.rowBackground ?? undefined,
                              color: effects.rowText ?? undefined,
                            }
                      }
                      onMouseDown={(event) => {
                        // REQ-108: shift-extension highlights ROWS only —
                        // preventing mousedown default stops the browser
                        // text-selecting every cell in the range. (First
                        // attempt silently no-op'd on a bad patch anchor and
                        // a vacuous synthetic-event check passed it; the
                        // Playwright gate caught it — keep real-input tests.)
                        if (event.shiftKey) {
                          event.preventDefault();
                        }
                      }}
                      onClick={(event) => {
                        setFocusedRow(index);
                        if (event.shiftKey) {
                          // REQ-023: shift EXTENDS — every row between the
                          // anchor (the focused row) and the click joins the
                          // selection; ctrl toggles one row.
                          const from = Math.min(focusedRow, index);
                          const to = Math.max(focusedRow, index);
                          const range = rowsState.rows
                            .slice(from, to + 1)
                            .map((member) => member.recordId);
                          setSelection((sel) => ({
                            kind: "explicit",
                            recordIds: [
                              ...new Set([
                                ...(sel.kind === "explicit" ? sel.recordIds : []),
                                ...range,
                              ]),
                            ],
                          }));
                        } else if (event.ctrlKey) {
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
                      {panel.columns.map((column) => {
                        // Every cell renders through the ONE formatter,
                        // keyed by the column's declared format kind — raw
                        // SQL values never reach the grid (D1).
                        const text = formatCell(
                          row.values[column.fieldName],
                          column.format,
                        );
                        const accent = effects.accentByField[column.fieldName];
                        return (
                          <td
                            key={column.fieldName}
                            style={{ textAlign: column.alignment }}
                          >
                            {accent === undefined ? (
                              text
                            ) : (
                              // The accent effect's rendering (D7): the
                              // prototype's status chip, colored entirely by
                              // the rule's status slot — the CSS custom
                              // property carries the var() reference so the
                              // chip repaints with the active template
                              // (REQ-045: slots, never literal colors).
                              <span
                                className="status-chip slot-colored"
                                style={{ "--chip-color": accent } as CSSProperties}
                              >
                                {text}
                              </span>
                            )}
                          </td>
                        );
                      })}
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

        {/* Region 3: the status bar (D12) — the middle is reserved for
            progress; the whole-set counts sit LAST so the bar's last-child
            rule pushes them to the right edge, matching the prototype. */}
        <div className="grid-status-bar">
          <span className="grid-status-progress" role="status">
            {rowsState.loading ? "Loading grid" : ""}
          </span>
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
        ) : DomainPreview !== null ? (
          // The per-source domain preview (preview-seam): the engagement
          // sources render the rollup-led REQ-073/088 content here.
          <DomainPreview recordId={previewRecordId} refreshToken={generation} />
        ) : activeView?.entityType != null ? (
          <RecordPreview
            entityType={activeView.entityType}
            recordId={previewRecordId}
          />
        ) : (
          <p className="grid-preview-hint">
            This view's rows come from a combined source with no single record behind
            them, so there is nothing more to preview than the row itself.
          </p>
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
