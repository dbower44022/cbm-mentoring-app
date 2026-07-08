/**
 * Hand-written wire shapes for the view-model payloads WTK-198 renders. The
 * Python routers are the contract (home.py, shell.py, records.py serve these
 * exact camelCase names); this module only names them for the compiler,
 * following the session.ts precedent — the endpoints type these payloads as
 * free-form envelope data, so the generated schema.d.ts cannot carry them.
 */

/** The app-wide educate-voice message shape (auth_flows.EducateMessage). */
export interface EducatePayload {
  whatHappened: string;
  why: string;
  whatNext: string;
}

/** One admin message with THIS user's state folded in (home.py). */
export interface AdminMessagePayload {
  messageKey: string;
  title: string;
  body: string;
  postedBy: string;
  postedAt: string;
  expiresAt: string | null;
  priority: "normal" | "urgent";
  requiresAcknowledgment: boolean;
  acknowledged: boolean;
}

/** One Home dashlet; `notice` set = broken but still shown (never dropped). */
export interface DashletPayload {
  viewKey: string;
  title: string;
  notice: EducatePayload | null;
}

/** `GET /home` — rendering it READS the messages it returns (REQ-011). */
export interface HomePayload {
  frame: {
    logoZone: string;
    identityZone: string;
    areasZone: string;
    headerRight: string[];
    accountMenu: { key: string; label: string }[];
  };
  areas: string[];
  dashlets: DashletPayload[];
  messages: AdminMessagePayload[];
}

/** `GET /home/banner` — urgent messages this user has NOT read. */
export interface BannerPayload {
  messages: AdminMessagePayload[];
}

/** One unread bell entry (shell.py, REQ-014). */
export interface BellEntryPayload {
  notificationID: string;
  notificationType: string;
  notificationMessage: string;
  jobID: string | null;
  createdAt: string;
}

/** `GET /shell/bell` — `meta.unreadCount` is the badge number. */
export interface BellPayload {
  entries: BellEntryPayload[];
}

/** One option value as `GET /schema/{entity}` serves it (DB-S7). */
export interface OptionValuePayload {
  optionValueID: string;
  optionValueName: string;
  optionValueLabel: string;
  optionValueSortOrder: number;
  activeFlag: boolean;
}

export interface OptionSetPayload {
  optionSetID: string;
  optionSetName: string;
  optionValues: OptionValuePayload[];
}

/** One field's edit-form entry: settings + disposition (records.py). */
export interface FormFieldPayload {
  fieldName: string;
  fieldLabel: string;
  fieldType: string;
  requiredFlag: boolean;
  validationRules: Record<string, unknown> | null;
  defaultValue: unknown;
  helpText: string | null;
  optionSet: OptionSetPayload | null;
  editable: boolean;
  /** Set exactly when not editable: kind + the click-to-explain message (REQ-039). */
  readOnly: {
    kind: string;
    explanation: EducatePayload;
    rendering: { position: string; value: string; click: string; tabStop: boolean };
  } | null;
  /** Set exactly when field settings carry help text (REQ-040). */
  help: {
    helpText: string;
    rendering: {
      marker: string;
      placement: string;
      reveal: string[];
      persistent: boolean;
      tabStop: boolean;
    };
  } | null;
}

/** `GET /records/{entityType}/{recordId}/edit-form` (records.py, REQ-032). */
export interface EditFormPayload {
  screen: {
    presentation: string;
    fieldPositions: string;
    controlScale: string;
    initialFocus: string;
    escape: string;
    save: { label: string; prominence: string; shortcut: string };
    cancel: { label: string; behavior: string };
  };
  keyboard: {
    save: string;
    escapeFullForm: string;
    escapePerFieldWindow: string;
    enter: string;
    tab: string;
    shiftTab: string;
    initialFocus: string;
  };
  record: Record<string, unknown>;
  fields: FormFieldPayload[];
  initialFocusField: string | null;
}

/** `GET /records/{entityType}/create-form` (records.py, REQ-037). */
export interface CreateFormPayload {
  screen: EditFormPayload["screen"];
  keyboard: EditFormPayload["keyboard"];
  form: {
    kind: string;
    opens: string;
    prefillSource: string;
    validation: string;
    similarCheck: string;
    comparison: string;
    commits: string;
    landsOn: string;
    cancelCreates: string;
  };
  fields: FormFieldPayload[];
  /** Field-setting defaults — the form's initial values AND dirty baseline. */
  seed: Record<string, unknown>;
  /** Fields participating in any duplicate-match rule: they arm the check. */
  identityFieldNames: string[];
  initialFocusField: string | null;
}

/** One advisory-check candidate (POST .../similar-records, REQ-037). */
export interface SimilarCandidatePayload {
  record: Record<string, unknown>;
  /** True only for a soft-deleted match — the restore-instead offer. */
  removed: boolean;
}

export interface SimilarRecordsPayload {
  candidates: SimilarCandidatePayload[];
  matchedRuleNames: string[];
  blocking: boolean;
}

/** `GET /records/{entityType}/{recordId}/preview` (records.py, REQ-012). */
export interface RecordPreviewPayload {
  pane: {
    dockPosition: string;
    dockedWhen: string;
    readOptimized: boolean;
    editControls: boolean;
    editPaths: string[];
  };
  popOutFrame: {
    kind: string;
    hasNavigation: boolean;
    headerRight: string[];
  };
  record: Record<string, unknown>;
  notice: EducatePayload | null;
}
