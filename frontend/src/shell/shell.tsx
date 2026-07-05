/**
 * Window shell composition (DEC-080 §A–§C): every window boots the same
 * bundle; the route decides the kind. A main window renders data.mainWindow
 * with navigation; /record/:entityType/:recordId renders data.popOut
 * (record windows are not panel hosts). Both bind the quick-open shortcut
 * from the payload's own declaration and render the shell payload verbatim.
 */

import { type ReactElement, useCallback, useEffect, useState } from "react";
import {
  createSearchParams,
  Route,
  Routes,
  useNavigate,
  useParams,
} from "react-router-dom";
import { type ApiError, callApi, EnvelopeError } from "../api/envelope";
import { HomePanel } from "../panels/home";
import { type SessionState, userHeaders } from "../session";
import { UrgentBanner } from "./banner";
import { Header } from "./header";
import { Navigation } from "./navigation";
import type { PreferencePayload, ShellPayload } from "./payloads";
import { QuickOpen } from "./quick-open";

const NAVIGATION_PREFERENCE_KEY = "navigation";

interface ShellState {
  phase: "loading" | "ready" | "declined";
  shell: ShellPayload | null;
  errors: ApiError[];
}

export interface ShellProps {
  session: SessionState;
  onLoggedOut: () => void;
}

export function Shell({ session, onLoggedOut }: ShellProps): ReactElement {
  const [state, setState] = useState<ShellState>({
    phase: "loading",
    shell: null,
    errors: [],
  });
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  // Home's render reads its messages (REQ-011 auto-read on view); bumping
  // this token re-fetches the banner so it never banners what was just read.
  const [messagesViewedAt, setMessagesViewedAt] = useState(0);
  const onMessagesViewed = useCallback(() => {
    setMessagesViewedAt((current) => current + 1);
  }, []);
  const navigate = useNavigate();

  const loadShell = useCallback((): void => {
    void callApi<ShellPayload>("/shell", { headers: userHeaders(session) })
      .then(({ data }) => {
        setState({ phase: "ready", shell: data, errors: [] });
      })
      .catch((failure: unknown) => {
        if (failure instanceof EnvelopeError) {
          setState({ phase: "declined", shell: null, errors: failure.errors });
        } else {
          setState({
            phase: "declined",
            shell: null,
            errors: [
              {
                fieldName: null,
                code: "unreachable",
                message:
                  "The app could not reach its API. Check your connection and retry; if it persists, the service itself may be down.",
              },
            ],
          });
        }
      });
  }, [session]);

  useEffect(loadShell, [loadShell]);

  const shell = state.shell;

  // Ctrl+K binds identically on every window kind, from the payload's own
  // declaration (DEC-080 §A) — never a hardcoded key.
  useEffect(() => {
    if (shell === null) {
      return;
    }
    const shortcut = parseShortcut(shell.mainWindow.quickOpenShortcut);
    const onKeyDown = (event: KeyboardEvent): void => {
      if (
        event.key.toLowerCase() === shortcut.key &&
        event.ctrlKey === shortcut.ctrl &&
        event.shiftKey === shortcut.shift &&
        event.altKey === shortcut.alt
      ) {
        event.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [shell]);

  if (state.phase === "loading") {
    return <p className="shell-boot">Loading the app shell…</p>;
  }
  if (shell === null) {
    return (
      <div className="shell-boot">
        <p>The shell could not load:</p>
        <ul>
          {state.errors.map((error) => (
            <li key={`${error.code}:${error.fieldName ?? ""}`}>{error.message}</li>
          ))}
        </ul>
        <button type="button" onClick={loadShell}>
          Retry
        </button>
      </div>
    );
  }

  const openPanel = (panelKey: string, viewKey: string | null): void => {
    if (panelKey === shell.homePanelKey) {
      navigate("/");
      return;
    }
    const search =
      viewKey === null ? "" : `?${createSearchParams({ view: viewKey }).toString()}`;
    navigate(`/panel/${encodeURIComponent(panelKey)}${search}`);
  };

  const switchPresentation = (presentation: string): void => {
    // The navigation profile is ONE preference document (REQ-060 pair):
    // read the resolved document, change only the presentation, write the
    // whole thing back — PUT has no partial merge. No stored row yet means
    // the built-in default (tabs, no pins).
    void callApi<PreferencePayload>(`/preferences/${NAVIGATION_PREFERENCE_KEY}`, {
      headers: userHeaders(session),
    })
      .then(({ data }) => data.preferenceValue)
      .catch((failure: unknown) => {
        if (failure instanceof EnvelopeError && failure.status === 404) {
          return { pins: [] };
        }
        throw failure;
      })
      .then((document) =>
        callApi<PreferencePayload>(`/preferences/${NAVIGATION_PREFERENCE_KEY}`, {
          method: "PUT",
          headers: { ...userHeaders(session), "Content-Type": "application/json" },
          body: JSON.stringify({ preferenceValue: { ...document, presentation } }),
        }),
      )
      .then(loadShell);
  };

  const onMenuAction = (key: string): void => {
    if (key === "navigationStyle") {
      const options = shell.navigation.presentations;
      const current = shell.navigation.presentation;
      const next = options[(options.indexOf(current) + 1) % options.length];
      if (next !== undefined) {
        switchPresentation(next);
      }
      return;
    }
    // Interim educate notices while these surfaces are unbuilt or unserved
    // (Help's page→URL mapping is admin data no endpoint serves yet) —
    // never a dead control, never a hidden item.
    setNotice(
      key === "help"
        ? "No page-specific help exists yet for this panel. The help mapping is configured by administrators and hasn't shipped."
        : "This preference screen hasn't been built yet. It arrives with a later planning item; everything else keeps working.",
    );
  };

  const navigation = (
    <Navigation
      navigation={shell.navigation}
      session={session}
      onOpenPanel={openPanel}
      onNavigationStale={loadShell}
    />
  );

  return (
    <Routes>
      <Route
        path="/record/:entityType/:recordId"
        element={
          <PopOutWindow
            shell={shell}
            session={session}
            onLoggedOut={onLoggedOut}
            onMenuAction={onMenuAction}
            notice={notice}
            onDismissNotice={() => {
              setNotice(null);
            }}
            palette={
              paletteOpen ? (
                <QuickOpen
                  session={session}
                  onActivate={openPanel}
                  onClose={() => {
                    setPaletteOpen(false);
                  }}
                />
              ) : null
            }
          />
        }
      />
      <Route
        path="*"
        element={
          <div className="main-window">
            <UrgentBanner refreshToken={messagesViewedAt} />
            <Header
              header={shell.mainWindow}
              session={session}
              navigation={shell.mainWindow.hasNavigation ? navigation : undefined}
              onLoggedOut={onLoggedOut}
              onMenuAction={onMenuAction}
            />
            {notice !== null && (
              <p className="notice" role="status">
                {notice}{" "}
                <button
                  type="button"
                  onClick={() => {
                    setNotice(null);
                  }}
                >
                  Dismiss
                </button>
              </p>
            )}
            <main className="panel-host">
              <Routes>
                <Route
                  path="/"
                  element={<HomePanel onMessagesViewed={onMessagesViewed} />}
                />
                <Route path="/panel/:panelKey" element={<RoutedPanel />} />
              </Routes>
            </main>
            {paletteOpen && (
              <QuickOpen
                session={session}
                onActivate={openPanel}
                onClose={() => {
                  setPaletteOpen(false);
                }}
              />
            )}
          </div>
        }
      />
    </Routes>
  );
}

function RoutedPanel(): ReactElement {
  const { panelKey } = useParams();
  return <PanelPlaceholder panelKey={panelKey ?? ""} />;
}

function PanelPlaceholder({ panelKey }: { panelKey: string }): ReactElement {
  // Non-home panel content is later work: grids render with PI-003. The
  // shell routes here today so navigation is real end to end.
  return (
    <p className="panel-placeholder">
      Panel “{panelKey}” is open. Its content rendering lands with its own work task.
    </p>
  );
}

function PopOutWindow({
  shell,
  session,
  onLoggedOut,
  onMenuAction,
  notice,
  onDismissNotice,
  palette,
}: {
  shell: ShellPayload;
  session: SessionState;
  onLoggedOut: () => void;
  onMenuAction: (key: string) => void;
  notice: string | null;
  onDismissNotice: () => void;
  palette: ReactElement | null;
}): ReactElement {
  const { entityType, recordId } = useParams();
  return (
    <div className="pop-out-window">
      <Header
        header={shell.popOut}
        session={session}
        onLoggedOut={onLoggedOut}
        onMenuAction={onMenuAction}
      />
      {notice !== null && (
        <p className="notice" role="status">
          {notice}{" "}
          <button type="button" onClick={onDismissNotice}>
            Dismiss
          </button>
        </p>
      )}
      <main className="panel-host">
        <p className="panel-placeholder">
          Record window for {entityType ?? "?"}/{recordId ?? "?"}. The read-optimized
          record view renders with WTK-198.
        </p>
      </main>
      {palette}
    </div>
  );
}

function parseShortcut(declaration: string): {
  key: string;
  ctrl: boolean;
  shift: boolean;
  alt: boolean;
} {
  const parts = declaration.split("+").map((part) => part.trim().toLowerCase());
  const key = parts[parts.length - 1] ?? "k";
  return {
    key,
    ctrl: parts.includes("ctrl"),
    shift: parts.includes("shift"),
    alt: parts.includes("alt"),
  };
}
