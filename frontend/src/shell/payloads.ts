/**
 * The wire shapes the shell renders verbatim (DEC-080 §A–§F). Each mirrors
 * the payload composed in src/mentorapp/api/routers/shell.py — that router
 * and the ui/*.py view-models are the contract; nothing here re-derives
 * membership, marking, or menu content.
 */

export interface MenuItemPayload {
  key: string;
  label: string;
}

/** One StandardHeader declaration (mainWindow or popOut) from GET /shell. */
export interface HeaderPayload {
  left: string[];
  right: string[];
  accountMenu: MenuItemPayload[];
  quickOpenShortcut: string;
  hasNavigation: boolean;
}

export interface NavigationItemPayload {
  pinKey: string;
  label: string;
  panelKey: string;
  viewKey: string | null;
  isBroken: boolean;
}

export interface NavigationGroupPayload {
  label: string;
  items: NavigationItemPayload[];
}

export interface NavigationPayload {
  presentation: string;
  presentations: string[];
  groups: NavigationGroupPayload[];
}

export interface ShellPayload {
  mainWindow: HeaderPayload;
  popOut: HeaderPayload;
  homePanelKey: string;
  navigation: NavigationPayload;
}

export interface QuickOpenEntryPayload {
  kind: string;
  label: string;
  panelKey: string;
  viewKey: string | null;
}

export interface QuickOpenPayload {
  entries: QuickOpenEntryPayload[];
}

/** The app-wide educate triple: what happened → why → what next. */
export interface EducateMessagePayload {
  whatHappened: string;
  why: string;
  whatNext: string;
}

export interface BrokenPinDialogPayload {
  pinKey: string;
  reason: string;
  message: EducateMessagePayload;
  choices: string[];
}

/** POST /shell/navigation/pins/{pinKey}/open — exactly one side is set. */
export interface PinOpenPayload {
  opened: { panelKey: string; viewKey: string | null } | null;
  dialog: BrokenPinDialogPayload | null;
}

export interface PreferencePayload {
  preferenceValue: Record<string, unknown>;
}
