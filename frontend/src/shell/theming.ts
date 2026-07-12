/**
 * Effective-theme delivery (WTK-231, REQ-044/REQ-046): fetch the caller's
 * resolved app-wide theme — the org-default template replaced wholesale by
 * their template choice (REQ-044 layers one and two; the per-grid rowTheme
 * layer belongs to the grid render, not the shell) — and repaint the CSS
 * custom properties shell.css consumes. The backend resolves; this module
 * only transports and applies.
 */

import { callApi } from "../api/envelope";

/** GET /theming/effective — mirrors the WTK-230 payload. */
export interface EffectiveThemePayload {
  colorSlots: Record<string, string>;
  fontSlots: Record<
    string,
    { stepKey: string; fontFamily: string; fontWeight: number }
  >;
  typeScale: { scaleSteps: Record<string, number> };
}

/** The preference key the app-wide template choice rides (REQ-044 layer two,
 * mirrors ui/template_flow.py TEMPLATE_CHOICE_PREFERENCE_KEY). */
export const TEMPLATE_CHOICE_PREFERENCE_KEY = "theming.templateChoice";

/**
 * A font slot → CSS custom properties (REQ-046). Each slot exposes a family
 * and a weight; components pick the family/weight through these, and the size
 * comes from the slot's chosen type step (`--type-step-*`), never a raw size.
 */
export function fontSlotFamilyVariable(slotName: string): string {
  return `--font-${slotName.replace(/[A-Z]/g, (l) => `-${l.toLowerCase()}`)}-family`;
}

export function fontSlotWeightVariable(slotName: string): string {
  return `--font-${slotName.replace(/[A-Z]/g, (l) => `-${l.toLowerCase()}`)}-weight`;
}

/**
 * Record the caller's app-wide template choice (REQ-044 layer two) and
 * repaint. The choice rides the one preference mechanism (a
 * `{templateKey}` document under TEMPLATE_CHOICE_PREFERENCE_KEY); the server
 * re-resolves `/theming/effective` from it, so a re-apply is all the client
 * does. The key is a stored template's ID or a launch-set key.
 */
export async function chooseTemplate(templateKey: string): Promise<void> {
  await callApi(`/preferences/${TEMPLATE_CHOICE_PREFERENCE_KEY}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ preferenceValue: { templateKey } }),
  });
  await applyEffectiveTheme();
}

/**
 * The ONE slot-name → custom-property mapping. The backend's camelCase slot
 * vocabulary (storage.theming COLOR_SLOTS) derives mechanically into the
 * `--slot-*` kebab-case convention shell.css already uses
 * (rowBackground → --slot-row-background): deriving instead of enumerating
 * means the frontend never re-declares the slot list, so a slot added on the
 * backend needs no edit here — the vocabulary keeps its one canonical home.
 */
export function slotCssVariable(slotName: string): string {
  return `--slot-${slotName.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}`;
}

/**
 * Type-scale step → custom property (REQ-046). Step naming follows the
 * backend's step keys (xs/sm/md/lg/xl — storage.theming TYPE_SCALE_STEPS),
 * so the fetched scaleSteps document applies without translation.
 */
export function typeStepVariable(stepKey: string): string {
  return `--type-step-${stepKey}`;
}

/**
 * Apply the caller's effective theme to the document root — called once by
 * the Shell on boot. Colors fill the `--slot-*` properties; the shared type
 * scale fills the `--type-step-*` properties (REQ-046: components pick
 * steps, never arbitrary sizes).
 */
export async function applyEffectiveTheme(): Promise<void> {
  // WHOSE theme is the session's business: the envelope client sends the
  // session reference and the server resolves the user (FND-909 D9).
  let payload: EffectiveThemePayload;
  try {
    ({ data: payload } = await callApi<EffectiveThemePayload>("/theming/effective"));
  } catch {
    // Non-2xx or unreachable: keep the stylesheet defaults silently — the
    // shell.css :root values ARE the org-default Standard template's values,
    // so an unthemed boot still renders exactly layer one.
    return;
  }
  const root = document.documentElement;
  for (const [slot, color] of Object.entries(payload.colorSlots)) {
    root.style.setProperty(slotCssVariable(slot), color);
  }
  for (const [step, sizePx] of Object.entries(payload.typeScale.scaleSteps)) {
    root.style.setProperty(typeStepVariable(step), `${String(sizePx)}px`);
  }
  // Font slots (REQ-046): family + weight per slot; the size is the slot's
  // chosen step, already painted above — a font picks a step, never a size.
  for (const [slot, spec] of Object.entries(payload.fontSlots)) {
    root.style.setProperty(fontSlotFamilyVariable(slot), spec.fontFamily);
    root.style.setProperty(fontSlotWeightVariable(slot), String(spec.fontWeight));
  }
}
