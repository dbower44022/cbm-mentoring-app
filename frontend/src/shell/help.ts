/**
 * The ONE help resolution path (SKL-122, REQ-043): every Help affordance —
 * the floating icon, the menus' last-item Help, the grid menu's Help entry,
 * the workprocess frame's per-step Help — calls openHelp with the surface it
 * stands on, and openHelp asks GET /help/resolve where that surface's help
 * lives. Help content is OUTSIDE the app on the org's docs platform
 * (SKL-116): the answer is only ever a URL to open plus, for a generic
 * landing, the server's educate-voice notice.
 *
 * Inputs: the surface's coordinates (sourceType + sourceIdentifier — the
 * admin mapping's own vocabulary) and the caller's notice sink. Effects: a
 * separate browser tab opens at the resolved URL — NEVER a navigation of the
 * working window — and the notice, when the server serves one, lands in the
 * caller's existing notice mechanism. Failure mode: an unreachable resolver
 * surfaces its own educate notice; Help never dead-ends silently.
 */

import { callApi } from "../api/envelope";

/** The mapping's surface kinds — mirrors HELP_SOURCE_TYPES (storage/help.py). */
export type HelpSourceType = "panel" | "dataSet" | "workprocess";

/** The GET /help/resolve answer (api/routers/help.py is the contract). */
export interface HelpResolution {
  /** Where Help opens; null only when help is entirely unconfigured. */
  url: string | null;
  /** True when the URL is page-specific (mapping row or pattern-derived). */
  mapped: boolean;
  /** The educate-voice explanation of a generic landing, when one applies. */
  notice: string | null;
}

/** What openHelp says when the resolver itself cannot be reached. */
export const HELP_UNREACHABLE_NOTICE =
  "Help couldn't be reached right now. Try again shortly; if it keeps failing, " +
  "the app's API may be down.";

/**
 * Resolve the surface's help URL and open it in a separate tab, surfacing
 * any notice through the caller's notice mechanism.
 */
export async function openHelp(
  sourceType: HelpSourceType,
  sourceIdentifier: string,
  onNotice: (notice: string) => void,
): Promise<void> {
  let resolution: HelpResolution;
  try {
    const query = new URLSearchParams({ sourceType, sourceIdentifier });
    ({ data: resolution } = await callApi<HelpResolution>(
      `/help/resolve?${query.toString()}`,
    ));
  } catch {
    // Never a dead-end and never silent (REQ-043): a failed resolution
    // explains itself in the same voice a generic landing does.
    onNotice(HELP_UNREACHABLE_NOTICE);
    return;
  }
  if (resolution.url !== null) {
    // A separate browser window/tab, never navigating the working window
    // (REQ-043). noopener severs the opener handle so the external docs
    // site cannot script back into the app window.
    window.open(resolution.url, "_blank", "noopener");
  }
  if (resolution.notice !== null) {
    // Generic landing (or unconfigured help): the server's educate words,
    // verbatim, through whatever notice surface the caller already has.
    onNotice(resolution.notice);
  }
}
