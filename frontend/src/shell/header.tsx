/**
 * The standard header (DEC-080 §C): one thin bar rendering the GET /shell
 * header declaration verbatim — zones by stable key in payload order, the
 * account menu items as served (Help last, the app-wide rule the server
 * pins). The Log out item ends the session totally across windows: no
 * editors ship yet (PI-003+), so there is no unsaved work to guard; the
 * WindowSessionController port (WTK-199) owns the dirty guard when they land.
 */

import { type ReactElement, useEffect, useRef, useState } from "react";
import { callApi } from "../api/envelope";
import { broadcastSessionLoggedOut, clearSession, type SessionState } from "../session";
import { NotificationBell } from "./bell";
import type { HeaderPayload, MenuItemPayload } from "./payloads";

export interface HeaderProps {
  header: HeaderPayload;
  session: SessionState;
  /** The main window's navigation, slotted into its declared zone. */
  navigation?: ReactElement | undefined;
  onLoggedOut: () => void;
  onMenuAction: (key: string) => void;
}

export function Header({
  header,
  session,
  navigation,
  onLoggedOut,
  onMenuAction,
}: HeaderProps): ReactElement {
  const logout = (): void => {
    void callApi<{ loggedOut: boolean }>("/auth/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionReference: session.sessionReference }),
    }).finally(() => {
      // Logout always succeeds server-side ("not signed in" is the goal
      // state either way), so the local end is unconditional and total.
      clearSession();
      broadcastSessionLoggedOut(session.userID);
      onLoggedOut();
    });
  };

  const zones: Record<string, () => ReactElement | null> = {
    identity: () => (
      <span key="identity" className="header-identity">
        CBM Mentoring
      </span>
    ),
    navigation: () => navigation ?? null,
    notificationBell: () => <NotificationBell key="notificationBell" />,
    help: () => (
      <button
        key="help"
        type="button"
        className="header-help"
        onClick={() => {
          onMenuAction("help");
        }}
      >
        Help
      </button>
    ),
    accountMenu: () => (
      <AccountMenu
        key="accountMenu"
        items={header.accountMenu}
        onSelect={(key) => {
          if (key === "logout") {
            logout();
          } else {
            onMenuAction(key);
          }
        }}
      />
    ),
  };

  const renderZone = (zone: string): ReactElement | null => zones[zone]?.() ?? null;

  return (
    <header className="shell-header">
      <div className="header-left">{header.left.map(renderZone)}</div>
      <div className="header-right">{header.right.map(renderZone)}</div>
    </header>
  );
}

function AccountMenu({
  items,
  onSelect,
}: {
  items: MenuItemPayload[];
  onSelect: (key: string) => void;
}): ReactElement {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    const closeOnOutsideClick = (event: MouseEvent): void => {
      if (rootRef.current !== null && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="account-menu">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => {
          setOpen((wasOpen) => !wasOpen);
        }}
      >
        Account ▾
      </button>
      {open && (
        <ul role="menu" className="account-menu-items">
          {items.map((item) => (
            <li key={item.key} role="none">
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  onSelect(item.key);
                }}
              >
                {item.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
