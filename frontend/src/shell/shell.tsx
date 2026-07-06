/**
 * Window shell composition (DEC-080 §A–§C): every window boots the same
 * bundle; the route decides the kind. The shell hosts main windows
 * (navigation + panel host); pop-out record windows are app.tsx's
 * /records/... route rendering windows/record.tsx — the one canonical
 * home (WTK-228). The quick-open shortcut binds from the payload's own
 * declaration and the shell payload renders verbatim.
 */

import { type ReactElement, useCallback, useEffect, useState } from "react";
import {
  createSearchParams,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import { type ApiError, callApi, EnvelopeError } from "../api/envelope";
import { GridPanel } from "../grid/grid-panel";
import { HomePanel } from "../panels/home";
import { type SessionState, userHeaders } from "../session";
import { UrgentBanner } from "./banner";
import { Header } from "./header";
import { openHelp } from "./help";
import { Navigation } from "./navigation";
import { PanelSplitter, ResizablePanel, usePanelChrome } from "./panel-chrome";
import type { PreferencePayload, ShellPayload } from "./payloads";
import { QuickOpen } from "./quick-open";
import { applyEffectiveTheme } from "./theming";

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
  // Panel sizes + per-panel zoom persist per user (REQ-087, WTK-225).
  const panelChrome = usePanelChrome(session);
  const onMessagesViewed = useCallback(() => {
    setMessagesViewedAt((current) => current + 1);
  }, []);
  const navigate = useNavigate();
  const location = useLocation();

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

  // Deliver the user's effective theme once per boot (WTK-231, REQ-044):
  // repaints the CSS slot/step variables app-wide. On failure the stylesheet
  // defaults stay in force — they are the org-default Standard values — so
  // theming never gates the shell render.
  useEffect(() => {
    void applyEffectiveTheme(session);
  }, [session]);

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

  // The shell's page identity for Help (REQ-043): the route IS the panel —
  // /panel/:panelKey names the routed panel, everything else is the home
  // panel. Read from the location rather than component state so the header
  // menu and the floating icon agree with whatever the panel host shows.
  const currentPanelKey = (): string => {
    const routed = /^\/panel\/([^/]+)/.exec(location.pathname);
    return routed?.[1] !== undefined
      ? decodeURIComponent(routed[1])
      : shell.homePanelKey;
  };

  const openPageHelp = (): void => {
    // The one resolution path (SKL-122): mapping → pattern → home, opened in
    // a separate tab; a generic landing's educate notice rides the shell's
    // existing notice surface.
    void openHelp("panel", currentPanelKey(), setNotice);
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
    if (key === "help") {
      openPageHelp();
      return;
    }
    // Interim educate notice while the preference screens are unbuilt —
    // never a dead control, never a hidden item.
    setNotice(
      "This preference screen hasn't been built yet. It arrives with a later planning item; everything else keeps working.",
    );
  };

  const sideNavigation = shell.navigation.presentation !== "tabs";

  const navigation = (
    <Navigation
      navigation={shell.navigation}
      session={session}
      onOpenPanel={openPanel}
      onNavigationStale={loadShell}
    />
  );

  // Pop-out record windows are app.tsx's /records/... route (RecordWindow,
  // windows/record.tsx) — the one canonical home; the shell hosts only
  // main windows (WTK-228 rework, SKL-122).
  return (
    <Routes>
      <Route
        path="*"
        element={
          <div className="main-window">
            <UrgentBanner refreshToken={messagesViewedAt} />
            <Header
              header={shell.mainWindow}
              session={session}
              navigation={
                shell.mainWindow.hasNavigation && !sideNavigation
                  ? navigation
                  : undefined
              }
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
            {/* Side-presentation navigation is a real left PANEL (SKL-113),
                resizable through the wide splitter and zoomable like every
                panel (REQ-087) — only the tabs presentation lives in the
                header bar. */}
            <div className="main-body">
              {sideNavigation && (
                <ResizablePanel
                  panelKey="navigation"
                  savedWidth={panelChrome.document.widths.navigation}
                  zoom={panelChrome.document.zooms.navigation ?? 100}
                  onZoom={panelChrome.saveZoom}
                  className="nav-panel"
                >
                  {navigation}
                </ResizablePanel>
              )}
              {sideNavigation && (
                <PanelSplitter panelKey="navigation" onResize={panelChrome.saveWidth} />
              )}
              <ResizablePanel
                panelKey="mainPanel"
                savedWidth={undefined}
                zoom={panelChrome.document.zooms.mainPanel ?? 100}
                onZoom={panelChrome.saveZoom}
                className="main-panel"
              >
                <main className="panel-host">
                  <Routes>
                    <Route
                      path="/"
                      element={<HomePanel onMessagesViewed={onMessagesViewed} />}
                    />
                    <Route path="/panel/:panelKey" element={<RoutedPanel />} />
                  </Routes>
                </main>
              </ResizablePanel>
            </div>
            {paletteOpen && (
              <QuickOpen
                session={session}
                onActivate={openPanel}
                onClose={() => {
                  setPaletteOpen(false);
                }}
              />
            )}
            {/* The floating Help icon (REQ-043): on EVERY main-window page,
                fixed so it survives scroll, resolving the same page identity
                through the same path as the menus' Help — one mapping, one
                resolver, never a hidden icon. */}
            <button
              type="button"
              className="floating-help"
              aria-label="Help for this page"
              title="Help for this page"
              onClick={openPageHelp}
            >
              ?
            </button>
          </div>
        }
      />
    </Routes>
  );
}

function RoutedPanel(): ReactElement {
  const { panelKey } = useParams();
  // Every non-home panel is a grid panel until other panel types (dashboard,
  // Gantt, chart) ship — the grid renders its own four states, so a panel
  // whose surface isn't served yet shows the educate-voice error, never a blank.
  return <GridPanel panelKey={panelKey ?? ""} />;
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
