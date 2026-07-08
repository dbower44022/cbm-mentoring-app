/**
 * The Themes surface (REQ-044/046) — the account menu's "Themes" entry.
 * Colors are controlled by TEMPLATES with a fixed slot structure, never
 * per-element freeform styling (REQ-044): the picker chooses the app-wide
 * template (layer two over the org default), and the creator builds a user
 * template by filling the fixed slots. The backend
 * (`/theming/templates`, `/theming/effective`) resolves and validates; this
 * surface renders those contracts.
 *
 * - Picker: the whole live set — the curated launch templates (Standard,
 *   Compact, Large print, Dark) and the caller's own — each choosable;
 *   choosing records the layer-two preference and repaints (chooseTemplate).
 * - Creator: fills the 15 fixed color slots (chrome / row / status groups)
 *   and the 2 font slots, picking a base type STEP (never a raw size —
 *   REQ-046). Save POSTs the template; the response's `meta.contrastWarnings`
 *   render as warning cards with live previews and NEVER block the save
 *   (REQ-046 guardrail warns, never blocks). A saved template joins the
 *   picker and can be chosen.
 */

import { type ReactElement, useEffect, useState } from "react";

import { callApi } from "../api/envelope";
import { EducateNotice } from "./educate";
import { chooseTemplate } from "./theming";

// The fixed slot vocabulary (storage/theming.py COLOR_SLOTS, three groups).
// Enumerated here as the render order; the backend is the validation
// authority — an off-vocabulary slot would be refused there.
const CHROME_SLOTS = [
  "appBackground",
  "panelBackground",
  "headerBackground",
  "headerText",
  "accent",
] as const;
const ROW_SLOTS = [
  "rowBackground",
  "rowAlternateBackground",
  "rowText",
  "selectedRowBackground",
  "selectedRowText",
  "groupHeaderBackground",
  "groupHeaderText",
] as const;
const STATUS_SLOTS = ["statusPositive", "statusWarning", "statusNegative"] as const;
const FONT_SLOTS = ["uiFont", "dataFont"] as const;
const TYPE_STEPS = ["xs", "sm", "md", "lg", "xl"] as const;

/** A readable default so every slot starts from a valid color. */
const SLOT_SEED: Record<string, string> = {
  appBackground: "#f4f6f8",
  panelBackground: "#ffffff",
  headerBackground: "#1d3557",
  headerText: "#ffffff",
  accent: "#2a6f97",
  rowBackground: "#ffffff",
  rowAlternateBackground: "#f0f4f8",
  rowText: "#1a1a1a",
  selectedRowBackground: "#d6e6f2",
  selectedRowText: "#102a43",
  groupHeaderBackground: "#e3e9ef",
  groupHeaderText: "#243b53",
  statusPositive: "#2d6a4f",
  statusWarning: "#b45309",
  statusNegative: "#b02a37",
};

interface TemplateSummary {
  colorTemplateID: string;
  colorTemplateName: string;
  templateType: "system" | "user";
  launchSetKey: string | null;
  colorSlots: Record<string, string>;
  rowVersion: number;
}

interface EducatePayload {
  whatHappened: string;
  why: string;
  whatNext: string;
}

interface FontSpec {
  fontFamily: string;
  stepKey: string;
}

interface ContrastWarning {
  kind: "readability" | "banding";
  ratioLabel: string;
  preview: {
    textColor?: string;
    backgroundColor?: string;
    baseBackground?: string;
    alternateBackground?: string;
    sampleText: string;
  };
  message: EducatePayload;
}

const HUMAN_SLOT: Record<string, string> = {
  appBackground: "App background",
  panelBackground: "Panel background",
  headerBackground: "Header background",
  headerText: "Header text",
  accent: "Accent",
  rowBackground: "Row background",
  rowAlternateBackground: "Alternate row",
  rowText: "Row text",
  selectedRowBackground: "Selected row",
  selectedRowText: "Selected row text",
  groupHeaderBackground: "Group header",
  groupHeaderText: "Group header text",
  statusPositive: "Status: positive",
  statusWarning: "Status: warning",
  statusNegative: "Status: negative",
  uiFont: "Interface font",
  dataFont: "Data font",
};

export function Appearance({ onClose }: { onClose: () => void }): ReactElement {
  const [templates, setTemplates] = useState<TemplateSummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [chosenKey, setChosenKey] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const load = (): void => {
    void callApi<TemplateSummary[]>("/theming/templates")
      .then(({ data }) => {
        setTemplates(data);
        setLoadError(null);
      })
      .catch(() => {
        setLoadError("Themes couldn't be loaded. Try reopening in a moment.");
      });
  };
  useEffect(load, []);

  const pick = (template: TemplateSummary): void => {
    // System launch templates are chosen by their launch-set key; user
    // templates by their ID (both forms the effective-theme resolver accepts).
    const key =
      template.templateType === "system" && template.launchSetKey !== null
        ? template.launchSetKey
        : template.colorTemplateID;
    setChosenKey(key);
    void chooseTemplate(key);
  };

  return (
    <div className="appearance-overlay" role="dialog" aria-label="Themes">
      <div className="appearance-panel">
        <header className="appearance-header">
          <h2>Themes</h2>
          <button type="button" className="appearance-close" onClick={onClose}>
            Close
          </button>
        </header>

        {loadError !== null && (
          <p role="alert" className="appearance-error">
            {loadError}
          </p>
        )}

        {creating ? (
          <TemplateCreator
            onCancel={() => {
              setCreating(false);
            }}
            onCreated={() => {
              setCreating(false);
              load();
            }}
          />
        ) : (
          <>
            <p className="appearance-intro">
              Pick a theme to apply it everywhere, or build your own by filling the
              fixed slots — colours come from templates, never per-screen styling.
            </p>
            <ul className="template-picker">
              {(templates ?? []).map((template) => (
                <li key={template.colorTemplateID}>
                  <button
                    type="button"
                    className="template-card"
                    aria-pressed={
                      chosenKey === (template.launchSetKey ?? template.colorTemplateID)
                    }
                    onClick={() => {
                      pick(template);
                    }}
                  >
                    <span
                      className="template-swatch"
                      aria-hidden="true"
                      style={{
                        background: template.colorSlots.panelBackground,
                        borderColor: template.colorSlots.accent,
                      }}
                    >
                      <span
                        style={{ background: template.colorSlots.headerBackground }}
                      />
                      <span style={{ background: template.colorSlots.accent }} />
                      <span
                        style={{
                          background: template.colorSlots.rowAlternateBackground,
                        }}
                      />
                    </span>
                    <span className="template-name">{template.colorTemplateName}</span>
                    <span className="template-kind">
                      {template.templateType === "system" ? "Built-in" : "Yours"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <button
              type="button"
              className="template-new"
              onClick={() => {
                setCreating(true);
              }}
            >
              New template…
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function TemplateCreator({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: () => void;
}): ReactElement {
  const [name, setName] = useState("");
  const [colors, setColors] = useState<Record<string, string>>({ ...SLOT_SEED });
  const [fonts, setFonts] = useState<Record<string, FontSpec>>({
    uiFont: { fontFamily: "system-ui", stepKey: "md" },
    dataFont: { fontFamily: "system-ui", stepKey: "md" },
  });
  const fontSpec = (slot: string): FontSpec =>
    fonts[slot] ?? { fontFamily: "system-ui", stepKey: "md" };
  const [sizeStep, setSizeStep] = useState<string>("md");
  const [warnings, setWarnings] = useState<ContrastWarning[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  const save = async (): Promise<void> => {
    if (saving) {
      return;
    }
    setSaving(true);
    setErrors([]);
    try {
      const result = await callApi<TemplateSummary>("/theming/templates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          colorTemplateName: name,
          colorSlots: colors,
          fontSlots: Object.fromEntries(
            FONT_SLOTS.map((slot) => [
              slot,
              {
                stepKey: fontSpec(slot).stepKey,
                fontFamily: fontSpec(slot).fontFamily,
                fontWeight: 400,
              },
            ]),
          ),
          typeStepChoice: sizeStep,
        }),
      });
      // The guardrail review rides meta, never errors (REQ-046): warnings are
      // shown but the save already succeeded. Surface them, then finish.
      const meta = result.meta as { contrastWarnings?: ContrastWarning[] };
      const shown = meta.contrastWarnings ?? [];
      if (shown.length > 0) {
        setWarnings(shown);
        setSaving(false);
        return; // let the user read the warnings; the template is saved
      }
      onCreated();
    } catch (failure: unknown) {
      // Structure violations (off-scale, bad slots, duplicate name) are the
      // only things that refuse a save.
      const messages =
        failure && typeof failure === "object" && "errors" in failure
          ? (failure as { errors: { message: string }[] }).errors.map((e) => e.message)
          : ["The template couldn't be saved. Check the fields and try again."];
      setErrors(messages);
      setSaving(false);
    }
  };

  return (
    <div className="template-creator">
      <label className="creator-field">
        <span>Template name</span>
        <input
          type="text"
          value={name}
          onChange={(event) => {
            setName(event.target.value);
          }}
        />
      </label>

      {warnings.length > 0 && (
        <div className="contrast-warnings" role="status">
          <p className="contrast-warnings-lead">
            Saved. Some colour combinations may be hard to read — you can adjust them or
            keep them:
          </p>
          {warnings.map((warning, index) => (
            <div key={`${warning.kind}:${String(index)}`} className="contrast-card">
              <span
                className="contrast-preview"
                aria-hidden="true"
                style={{
                  color: warning.preview.textColor,
                  background:
                    warning.preview.backgroundColor ?? warning.preview.baseBackground,
                }}
              >
                {warning.preview.sampleText}
              </span>
              <span className="contrast-ratio">{warning.ratioLabel}</span>
              <EducateNotice notice={warning.message} />
            </div>
          ))}
          <div className="creator-actions">
            <button type="button" onClick={onCreated}>
              Keep and close
            </button>
          </div>
        </div>
      )}

      {errors.length > 0 && (
        <ul role="alert" className="creator-errors">
          {errors.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      )}

      {warnings.length === 0 && (
        <>
          <fieldset className="slot-group">
            <legend>Chrome</legend>
            {CHROME_SLOTS.map((slot) => (
              <ColorSlotField
                key={slot}
                slot={slot}
                value={colors[slot] ?? ""}
                onChange={(value) => {
                  setColors((c) => ({ ...c, [slot]: value }));
                }}
              />
            ))}
          </fieldset>
          <fieldset className="slot-group">
            <legend>Grid rows</legend>
            {ROW_SLOTS.map((slot) => (
              <ColorSlotField
                key={slot}
                slot={slot}
                value={colors[slot] ?? ""}
                onChange={(value) => {
                  setColors((c) => ({ ...c, [slot]: value }));
                }}
              />
            ))}
          </fieldset>
          <fieldset className="slot-group">
            <legend>Status</legend>
            {STATUS_SLOTS.map((slot) => (
              <ColorSlotField
                key={slot}
                slot={slot}
                value={colors[slot] ?? ""}
                onChange={(value) => {
                  setColors((c) => ({ ...c, [slot]: value }));
                }}
              />
            ))}
          </fieldset>
          <fieldset className="slot-group">
            <legend>Fonts &amp; size</legend>
            {FONT_SLOTS.map((slot) => (
              <label key={slot} className="font-slot-field">
                <span>{HUMAN_SLOT[slot]}</span>
                <input
                  type="text"
                  aria-label={`${HUMAN_SLOT[slot] ?? slot} family`}
                  value={fontSpec(slot).fontFamily}
                  onChange={(event) => {
                    const value = event.target.value;
                    setFonts((f) => ({
                      ...f,
                      [slot]: { ...fontSpec(slot), fontFamily: value },
                    }));
                  }}
                />
                <select
                  aria-label={`${HUMAN_SLOT[slot] ?? slot} step`}
                  value={fontSpec(slot).stepKey}
                  onChange={(event) => {
                    const value = event.target.value;
                    setFonts((f) => ({
                      ...f,
                      [slot]: { ...fontSpec(slot), stepKey: value },
                    }));
                  }}
                >
                  {TYPE_STEPS.map((step) => (
                    <option key={step} value={step}>
                      {step}
                    </option>
                  ))}
                </select>
              </label>
            ))}
            <label className="font-slot-field">
              <span>Base size step</span>
              {/* Only defined steps — never an arbitrary size (REQ-046). */}
              <select
                aria-label="Base size step"
                value={sizeStep}
                onChange={(event) => {
                  setSizeStep(event.target.value);
                }}
              >
                {TYPE_STEPS.map((step) => (
                  <option key={step} value={step}>
                    {step}
                  </option>
                ))}
              </select>
            </label>
          </fieldset>

          <div className="creator-actions">
            <button
              type="button"
              className="creator-save"
              onClick={() => {
                void save();
              }}
            >
              Save template
            </button>
            <button type="button" onClick={onCancel}>
              Cancel
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function ColorSlotField({
  slot,
  value,
  onChange,
}: {
  slot: string;
  value: string;
  onChange: (value: string) => void;
}): ReactElement {
  return (
    <label className="color-slot-field">
      <span>{HUMAN_SLOT[slot] ?? slot}</span>
      <input
        type="color"
        aria-label={HUMAN_SLOT[slot] ?? slot}
        value={value || "#000000"}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
    </label>
  );
}
