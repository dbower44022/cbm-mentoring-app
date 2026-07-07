/**
 * Panel chrome (REQ-087 / SKL-113 v2, WTK-225 rework): every boundary
 * between panels is a wide, clearly grabbable splitter, and every panel
 * carries a user-set zoom level. Both persist per user in ONE preference
 * document (`panelChrome`) — read whole, changed in place, written whole,
 * because PUT /preferences has no partial merge (the REQ-060 pair rule).
 * Writes debounce so a drag is one write, not a stream.
 */

import {
  type ReactElement,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { callApi, EnvelopeError } from "../api/envelope";
import type { SessionState } from "../session";
import type { PreferencePayload } from "./payloads";

const PANEL_CHROME_PREFERENCE_KEY = "panelChrome";
const WRITE_DEBOUNCE_MS = 400;
export const ZOOM_MIN = 60;
export const ZOOM_MAX = 160;
export const ZOOM_STEP = 10;

interface PanelChromeDocument {
  widths: Record<string, number>;
  zooms: Record<string, number>;
}

const EMPTY_DOCUMENT: PanelChromeDocument = { widths: {}, zooms: {} };

/** Clamp a zoom percentage to the shared step range (REQ-087). */
export function clampZoom(value: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, value));
}

/**
 * The one loader/writer pair for the panelChrome preference document.
 * A missing document (404) is the built-in default: no saved sizes, 100%.
 */
export function usePanelChrome(session: SessionState | null): {
  document: PanelChromeDocument;
  saveWidth: (panelKey: string, width: number) => void;
  saveZoom: (panelKey: string, zoom: number) => void;
} {
  const [document, setDocument] = useState<PanelChromeDocument>(EMPTY_DOCUMENT);
  const documentRef = useRef(document);
  documentRef.current = document;
  const writeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load once per LOGIN, not per render: callers may pass a freshly-read
  // session object each render, and object-identity deps would refire this
  // effect and clobber unsaved local widths with the server copy (the
  // "splitter snaps back" defect, FND-909 follow-on).
  const sessionKey = session === null ? null : session.sessionReference;
  useEffect(() => {
    if (session === null) {
      // No session, no persistence: defaults render; nothing to load.
      return;
    }
    void callApi<PreferencePayload>(`/preferences/${PANEL_CHROME_PREFERENCE_KEY}`)
      .then(({ data }) => {
        const value = data.preferenceValue as Partial<PanelChromeDocument>;
        setDocument({
          widths: value.widths ?? {},
          zooms: value.zooms ?? {},
        });
      })
      .catch((failure: unknown) => {
        if (!(failure instanceof EnvelopeError && failure.status === 404)) {
          throw failure;
        }
      });
    // sessionKey (not the session object) is the deliberate dependency: the
    // object may be minted fresh per render while the login it names is
    // unchanged.
  }, [sessionKey]);

  const scheduleWrite = useCallback((): void => {
    if (session === null) {
      return;
    }
    if (writeTimer.current !== null) {
      clearTimeout(writeTimer.current);
    }
    writeTimer.current = setTimeout(() => {
      void callApi<PreferencePayload>(`/preferences/${PANEL_CHROME_PREFERENCE_KEY}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preferenceValue: documentRef.current }),
      });
    }, WRITE_DEBOUNCE_MS);
  }, [session]);

  const saveWidth = useCallback(
    (panelKey: string, width: number): void => {
      setDocument((current) => ({
        ...current,
        widths: { ...current.widths, [panelKey]: Math.round(width) },
      }));
      scheduleWrite();
    },
    [scheduleWrite],
  );

  const saveZoom = useCallback(
    (panelKey: string, zoom: number): void => {
      setDocument((current) => ({
        ...current,
        zooms: { ...current.zooms, [panelKey]: clampZoom(zoom) },
      }));
      scheduleWrite();
    },
    [scheduleWrite],
  );

  return { document, saveWidth, saveZoom };
}

/**
 * The wide grabbable boundary between two panels. Dragging resizes the
 * panel to its LEFT (the `panelKey` owner); the saved width is restored by
 * the owning ResizablePanel on next open.
 */
export function PanelSplitter({
  panelKey,
  onResize,
  minWidth = 120,
  maxWidth = 640,
  resizes = "previous",
}: {
  panelKey: string;
  onResize: (panelKey: string, width: number) => void;
  minWidth?: number;
  maxWidth?: number;
  /** Which sibling the drag resizes — a right-docked panel resizes "next". */
  resizes?: "previous" | "next";
}): ReactElement {
  const [dragging, setDragging] = useState(false);

  const startDrag = (event: React.MouseEvent<HTMLDivElement>): void => {
    event.preventDefault();
    const target = event.currentTarget as HTMLElement;
    const owner = (
      resizes === "previous" ? target.previousElementSibling : target.nextElementSibling
    ) as HTMLElement | null;
    if (owner === null) {
      return;
    }
    setDragging(true);
    const startX = event.clientX;
    const startWidth = owner.getBoundingClientRect().width;
    const onMove = (move: MouseEvent): void => {
      // A "next" panel grows as the splitter moves LEFT (drag delta inverts).
      const delta = move.clientX - startX;
      const width = Math.min(
        maxWidth,
        Math.max(minWidth, startWidth + (resizes === "previous" ? delta : -delta)),
      );
      owner.style.width = `${String(width)}px`;
      owner.style.flex = "none";
    };
    const onUp = (): void => {
      setDragging(false);
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      onResize(panelKey, owner.getBoundingClientRect().width);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Drag to resize — panel size is remembered"
      className={dragging ? "panel-splitter panel-splitter-dragging" : "panel-splitter"}
      onMouseDown={startDrag}
    />
  );
}

/**
 * A panel wrapper carrying its saved width and zoom. Zoom applies to the
 * panel's contents via the non-standard-but-universal CSS `zoom` property
 * (the layout standard's per-panel zoom, not browser zoom).
 */
export function ResizablePanel({
  panelKey,
  savedWidth,
  zoom,
  onZoom,
  className,
  children,
}: {
  panelKey: string;
  savedWidth: number | undefined;
  zoom: number;
  onZoom: (panelKey: string, zoom: number) => void;
  className: string;
  children: ReactNode;
}): ReactElement {
  return (
    <div
      className={`resizable-panel ${className}`}
      style={
        savedWidth === undefined
          ? undefined
          : { width: `${String(savedWidth)}px`, flex: "none" }
      }
    >
      <div className="panel-zoom-content" style={{ zoom: zoom / 100 }}>
        {children}
      </div>
      <div className="panel-zoom-control" aria-label="Panel zoom">
        <button
          type="button"
          onClick={() => {
            onZoom(panelKey, zoom - ZOOM_STEP);
          }}
        >
          −
        </button>
        <span>{zoom}%</span>
        <button
          type="button"
          onClick={() => {
            onZoom(panelKey, zoom + ZOOM_STEP);
          }}
        >
          +
        </button>
      </div>
    </div>
  );
}
