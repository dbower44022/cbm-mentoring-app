/**
 * Navigation rendering (DEC-080 §F): three renderers — tabs, side menu,
 * group tree — over the SAME groups/items payload. Membership is
 * server-decided; a renderer only shapes it. Broken pins stay visible and
 * clickable with a subtle marking (never hidden, never disabled); activation
 * always asks POST /shell/navigation/pins/{pinKey}/open, which answers with
 * the panel to open or the educate dialog.
 */

import { type ReactElement, useState } from "react";
import { callApi, EnvelopeError } from "../api/envelope";
import { type SessionState, userHeaders } from "../session";
import type {
  BrokenPinDialogPayload,
  NavigationGroupPayload,
  NavigationItemPayload,
  NavigationPayload,
  PinOpenPayload,
} from "./payloads";

export interface NavigationProps {
  navigation: NavigationPayload;
  session: SessionState;
  onOpenPanel: (panelKey: string, viewKey: string | null) => void;
  /** A 404 on open is a cross-window race: the pin set changed under us. */
  onNavigationStale: () => void;
}

// The wire carries choice keys only; these labels are the layout standard's
// wording, an interim client constant while the educate/screen catalog is not
// on the wire (DEC-080 recorded gap — not license to invent behavior).
const CHOICE_LABELS: Record<string, string> = {
  removePin: "Remove this pin",
  chooseDifferentView: "Choose a different view",
};

export function Navigation({
  navigation,
  session,
  onOpenPanel,
  onNavigationStale,
}: NavigationProps): ReactElement {
  const [dialog, setDialog] = useState<BrokenPinDialogPayload | null>(null);
  const [gapNotice, setGapNotice] = useState<string | null>(null);

  const activate = (item: NavigationItemPayload): void => {
    void callApi<PinOpenPayload>(
      `/shell/navigation/pins/${encodeURIComponent(item.pinKey)}/open`,
      { method: "POST", headers: userHeaders(session) },
    )
      .then(({ data }) => {
        if (data.opened !== null) {
          onOpenPanel(data.opened.panelKey, data.opened.viewKey);
        } else if (data.dialog !== null) {
          setDialog(data.dialog);
        }
      })
      .catch((failure: unknown) => {
        if (failure instanceof EnvelopeError && failure.status === 404) {
          onNavigationStale();
        } else {
          throw failure;
        }
      });
  };

  const item = (entry: NavigationItemPayload): ReactElement => (
    <button
      key={entry.pinKey}
      type="button"
      className={entry.isBroken ? "nav-item nav-item-broken" : "nav-item"}
      onClick={() => {
        activate(entry);
      }}
    >
      {entry.label}
      {entry.isBroken && (
        <span className="nav-broken-mark" aria-label="This pin needs attention">
          {" ⚠"}
        </span>
      )}
    </button>
  );

  const renderers: Record<string, (groups: NavigationGroupPayload[]) => ReactElement> =
    {
      tabs: (groups) => (
        <nav className="nav-tabs" aria-label="Navigation">
          {groups.map((group) => (
            <span key={group.label} className="nav-tab-group" title={group.label}>
              {group.items.map(item)}
            </span>
          ))}
        </nav>
      ),
      sideMenu: (groups) => (
        <nav className="nav-side-menu" aria-label="Navigation">
          {groups.map((group) => (
            <div key={group.label} className="nav-group">
              <div className="nav-group-label">{group.label}</div>
              {group.items.map(item)}
            </div>
          ))}
        </nav>
      ),
      groupTree: (groups) => (
        <nav className="nav-group-tree" aria-label="Navigation">
          {groups.map((group) => (
            <details key={group.label} open>
              <summary className="nav-group-label">{group.label}</summary>
              {group.items.map(item)}
            </details>
          ))}
        </nav>
      ),
    };

  // An unknown presentation degrades to tabs, mirroring how the server parses
  // stale navigation documents: degrade, never leave a window without navigation.
  const render = renderers[navigation.presentation] ?? renderers.tabs;

  return (
    <>
      {render?.(navigation.groups)}
      {dialog !== null && (
        <BrokenPinDialog
          dialog={dialog}
          gapNotice={gapNotice}
          onChoice={() => {
            // Applying a choice has NO endpoint today (DEC-080 §F, recorded
            // FND gap): the server owns remove_pin/repoint_pin semantics, so
            // the client explains rather than re-deriving them over
            // PUT /preferences/navigation.
            setGapNotice(
              "This choice isn't available yet. Its behavior lives server-side and the endpoint hasn't shipped; the pin stays exactly as it is for now.",
            );
          }}
          onDismiss={() => {
            // Dismissing changes nothing (DEC-080 §F).
            setDialog(null);
            setGapNotice(null);
          }}
        />
      )}
    </>
  );
}

function BrokenPinDialog({
  dialog,
  gapNotice,
  onChoice,
  onDismiss,
}: {
  dialog: BrokenPinDialogPayload;
  gapNotice: string | null;
  onChoice: (choice: string) => void;
  onDismiss: () => void;
}): ReactElement {
  return (
    <div
      className="overlay"
      role="dialog"
      aria-modal="true"
      aria-label="This pin needs attention"
    >
      <div className="dialog">
        <p className="educate-what">{dialog.message.whatHappened}</p>
        <p className="educate-why">{dialog.message.why}</p>
        <p className="educate-next">{dialog.message.whatNext}</p>
        <div className="dialog-choices">
          {dialog.choices.map((choice) => (
            <button
              key={choice}
              type="button"
              onClick={() => {
                onChoice(choice);
              }}
            >
              {CHOICE_LABELS[choice] ?? choice}
            </button>
          ))}
          <button type="button" onClick={onDismiss}>
            Close
          </button>
        </div>
        {gapNotice !== null && <p className="notice">{gapNotice}</p>}
      </div>
    </div>
  );
}
