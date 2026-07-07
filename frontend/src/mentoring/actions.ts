/**
 * The mentoring action declarations the engagement/resource grids join into
 * their menus (WTK-168/169/178/183). These join client-side exactly as the
 * workprocess entries do (grid-panel composes them through the same
 * `actionMenus` fold) because the panel view-model endpoint is served
 * per-grid by the platform, while these actions are the mentoring DOMAIN's —
 * one home here, keyed by the data sources they act over. Never hidden:
 * every action is always listed; a status that can't take a transition gets
 * the server's educate refusal (REQ-075), and a bad selection gets the grid
 * standard's one explainer.
 */

import { type ActionPayload } from "../grid/payloads";

/** The engagement-triage data sources (access/mentoring.py seeded keys). */
export const ENGAGEMENT_SOURCE_KEYS: readonly string[] = [
  "mentorEngagements",
  "leadershipEngagements",
];

/** The resources-library data source (REQ-084). */
export const RESOURCE_SOURCE_KEYS: readonly string[] = ["mentorResources"];

/**
 * The REQ-075 lifecycle actions, classification per the action rules:
 * every transition is `modifying` — a status flip changes data but destroys
 * nothing (decline included: DEC-071 says decline is a status change only,
 * which is exactly why it is NOT `destructive`). Each confirms in its own
 * dialog before the POST.
 */
export const LIFECYCLE_ACTIONS: readonly ActionPayload[] = [
  {
    key: "acceptAssignment",
    label: "Accept Assignment",
    selectionContract: "single",
    classification: "modifying",
  },
  {
    key: "declineAssignment",
    label: "Decline Assignment",
    selectionContract: "single",
    classification: "modifying",
  },
  {
    key: "putOnHold",
    label: "Put On Hold",
    selectionContract: "single",
    classification: "modifying",
  },
  {
    key: "markDormant",
    label: "Mark Dormant",
    selectionContract: "single",
    classification: "modifying",
  },
  {
    // WTK-172: the way back out of the paused states (On Hold/Dormant).
    key: "resumeEngagement",
    label: "Resume Engagement",
    selectionContract: "single",
    classification: "modifying",
  },
];

/** Action key → the server's lifecycle transition key. */
const TRANSITIONS: Record<string, string> = {
  acceptAssignment: "accept",
  declineAssignment: "decline",
  putOnHold: "hold",
  markDormant: "dormant",
  resumeEngagement: "resume",
};

/** Compose-from-template (REQ-076/077): opens the dialog; nothing sends
 * until the mentor confirms inside it, so invocation itself is safe. */
export const SEND_EMAIL_ACTION: ActionPayload = {
  key: "sendTemplatedEmail",
  label: "Send Email (templated)",
  selectionContract: "single",
  classification: "safe",
};

/** Share-a-resource (REQ-084): the templated email carrying the link. */
export const SHARE_RESOURCE_ACTION: ActionPayload = {
  key: "shareResource",
  label: "Share with a Client",
  selectionContract: "single",
  classification: "safe",
};

/** The mentoring entries one data source's menus carry (possibly none). */
export function mentoringPanelActions(
  dataSourceKey: string | undefined,
): ActionPayload[] {
  if (dataSourceKey !== undefined && ENGAGEMENT_SOURCE_KEYS.includes(dataSourceKey)) {
    return [...LIFECYCLE_ACTIONS, SEND_EMAIL_ACTION];
  }
  if (dataSourceKey !== undefined && RESOURCE_SOURCE_KEYS.includes(dataSourceKey)) {
    return [SHARE_RESOURCE_ACTION];
  }
  return [];
}

/** The lifecycle transition an action key names, or null for non-lifecycle. */
export function lifecycleTransitionFor(actionKey: string): string | null {
  return TRANSITIONS[actionKey] ?? null;
}

/** Whether a data source's docked preview renders the engagement preview. */
export function isEngagementSource(dataSourceKey: string | undefined): boolean {
  return dataSourceKey !== undefined && ENGAGEMENT_SOURCE_KEYS.includes(dataSourceKey);
}
