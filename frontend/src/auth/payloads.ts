/**
 * Wire shapes GET /auth/screens serves (mirrors src/mentorapp/ui/auth_flows.py
 * — the declarations there are the contract; the shell renders them verbatim)
 * plus the session payload POST /auth/login and /auth/reauth return.
 */

export interface EducateMessagePayload {
  whatHappened: string;
  why: string;
  whatNext: string;
}

export interface ScreenFieldPayload {
  name: string;
  label: string;
  control: "text" | "password" | "email";
  readOnly: boolean;
}

export interface AuthScreenPayload {
  key: string;
  title: string;
  fields: ScreenFieldPayload[];
  submitLabel: string;
  links: string[];
  enterSubmits: boolean;
}

export interface AuthScreensPayload {
  screens: {
    login: AuthScreenPayload;
    forgotPassword: AuthScreenPayload;
    reauth: AuthScreenPayload;
  };
  messages: {
    signInRejected: EducateMessagePayload;
    signInCrmUnavailable: EducateMessagePayload;
    resetRequested: EducateMessagePayload;
    reauthPrompt: EducateMessagePayload;
    reauthWrongUser: EducateMessagePayload;
    sessionEnded: EducateMessagePayload;
  };
}

/** What /auth/login and /auth/reauth return; loginName is added client-side. */
export interface SessionPayload {
  sessionReference: string;
  userID: string;
  roleNames: string[];
}
