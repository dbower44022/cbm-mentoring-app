/**
 * The per-data-source preview renderer seam (WTK-168, REQ-073). WHY:
 * windows/record.tsx's RecordPreview is deliberately generic — a flat
 * field list off the records surface — while REQ-073/074 fix a DOMAIN
 * rendering for engagements (rollup-led, click-throughs, fill-to-space).
 * The grid keys its docked pane off the ACTIVE VIEW's data source, so the
 * seam maps data-source keys to domain renderers and the generic
 * RecordPreview stays the fallback for everything unmapped. Registering a
 * future domain preview is one entry here — grid-panel never grows
 * per-entity branches.
 */

import { type ComponentType } from "react";

import { ENGAGEMENT_SOURCE_KEYS } from "../mentoring/actions";
import { EngagementPreview } from "../mentoring/engagement-preview";

export interface PreviewRendererProps {
  recordId: string;
  /** Bumped by the host grid on data refresh (lifecycle actions, reloads)
      so the domain preview re-reads instead of showing pre-action state. */
  refreshToken?: unknown;
}

const RENDERERS: readonly (readonly [
  readonly string[],
  ComponentType<PreviewRendererProps>,
])[] = [[ENGAGEMENT_SOURCE_KEYS, EngagementPreview]];

/** The domain preview for one data source, or null for the generic pane. */
export function previewRendererFor(
  dataSourceKey: string | undefined,
): ComponentType<PreviewRendererProps> | null {
  if (dataSourceKey === undefined) {
    return null;
  }
  for (const [keys, renderer] of RENDERERS) {
    if (keys.includes(dataSourceKey)) {
      return renderer;
    }
  }
  return null;
}
