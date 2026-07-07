/**
 * The REQ-072 startup landing, applied ONCE per window boot (FND-909 D8).
 *
 * The shell used component state to remember that the startup target had
 * been honored, which resets whenever the shell remounts — so within one
 * browser window a later pass (sign-out → sign-in, a boundary remount)
 * re-ran the redirect and "/" could never settle on Home. The latch is
 * module state instead: a browser window evaluates this module exactly once
 * per page load, so "claimed" IS "once per window boot" — surviving every
 * React remount, with nothing persisted (no sessionStorage) to leak the
 * claim into the NEXT boot, where the redirect must fire again.
 */

/** True exactly once; every later claim in the same window boot is false. */
export type StartupLatch = () => boolean;

/** A fresh latch — the window one below, or a per-test instance. */
export function createStartupLatch(): StartupLatch {
  let claimed = false;
  return () => {
    if (claimed) {
      return false;
    }
    claimed = true;
    return true;
  };
}

/** THE latch for this window boot; the Shell claims it on first shell load. */
export const windowStartupLatch: StartupLatch = createStartupLatch();

/**
 * Where the startup target says this boot should navigate, or null to stay
 * put: only a non-Home target hijacks, and only from the boot route "/" — a
 * deep-linked window keeps its own destination.
 */
export function startupNavigation(
  startupPanelKey: string,
  homePanelKey: string,
  pathname: string,
): string | null {
  if (startupPanelKey === homePanelKey || pathname !== "/") {
    return null;
  }
  return `/panel/${encodeURIComponent(startupPanelKey)}`;
}
