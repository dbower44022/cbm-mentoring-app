"""SelectOrCreateColorTemplate management build (WTK-119): REQ-044/REQ-046.

The concrete template-management surface over WTK-113's flow design
(``ui.template_flow`` — picker order, draft invariants, review cards) and
WTK-112's semantics (``ui.theming`` — slots, precedence, guardrail), neither
of which this module re-decides:

- **The curated launch set (REQ-044):** :data:`LAUNCH_TEMPLATES` ships the
  four launch documents — Standard (the org-branded default), Compact
  (maximum data density), Large print (accessibility), Dark (night palette;
  the usual base for personal copies). All four fill the same slot
  structure, so template x panel combinations always work, and all four
  pass the contrast guardrail clean: the curated set never warns.
- **Wire conversion:** :func:`stored_template_option` turns a persisted
  ``colorTemplate`` record (WTK-111 entity / WTK-114 read shape) into the
  flow's :class:`TemplateOption`; :func:`template_create_payload` turns the
  :class:`FinishedTemplate` the flow hands over into the TEMPLATE_CREATE
  write body WTK-120's router submits. The persisted document carries font
  SPECS ({stepKey, fontFamily, fontWeight}) and no row height; the flow
  document carries families and a row height — the conversion owns that
  seam in both directions.
- **Never-hide management actions:** every action is always listed;
  :func:`decide_template_action` answers whether an invocation can run and,
  when it can't (owner-only verbs on a system template), the educate-voice
  explanation instead of a hidden or grayed control. Delete is destructive:
  its confirmation is honest about soft deletion (an administrator can
  restore; never "cannot be undone").
- **Slot-filling editor controls:** :func:`editor_step_controls` renders
  one step of the WTK-113 walkthrough as concrete controls — color slots,
  font slots, the type-scale step selector (steps labeled with their px
  sizes; never an arbitrary point size), and the row-height step —
  and :func:`apply_control_edit` commits an edit through the flow's
  mutators, so every change keeps the draft's always-valid invariant.
- **Per-grid row-theme override controls:** :func:`row_theme_controls`
  renders the view-settings ``rowTheme`` step prefilled from the RESOLVED
  theme (the user's actual template showing through), scoped to exactly
  what a row theme may touch; commit and review stay in ``template_flow``
  (:func:`~mentorapp.ui.template_flow.build_row_theme`,
  :func:`~mentorapp.ui.template_flow.review_row_theme`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from mentorapp.observability import get_logger
from mentorapp.storage.theming import TEMPLATE_TYPES
from mentorapp.ui.auth_flows import EducateMessage
from mentorapp.ui.template_flow import (
    ORIGIN_SYSTEM,
    ROW_THEME_CUSTOM,
    ROW_THEME_MARKS_VIEW_MODIFIED,
    ROW_THEME_SCOPE_NOTE,
    ROW_THEME_STANDARD,
    STEP_COLOR_SLOTS,
    FinishedTemplate,
    TemplateDraft,
    TemplateFlowError,
    TemplateOption,
    set_color_slot,
    set_font_slot,
    set_row_height,
    set_size_step,
)
from mentorapp.ui.theming import (
    FONT_SLOTS,
    LAUNCH_TEMPLATE_KEYS,
    ROW_HEIGHT_STEPS,
    ROW_THEME_COLOR_SLOTS,
    STANDARD_TEMPLATE,
    TYPE_SCALE_STEPS,
    ThemeLayers,
    resolve_effective_grid_theme,
    validate_template,
)

log = get_logger(__name__)


# --- The curated launch set (REQ-044) -------------------------------------------------

LAUNCH_TEMPLATE_NAMES: Final[dict[str, str]] = {
    "standard": "Standard",
    "compact": "Compact",
    "largePrint": "Large print",
    "dark": "Dark",
}


def _standard_variant(row_height: str, size_step: str) -> dict[str, Any]:
    # Compact and Large print are DENSITY variants of the org branding: same
    # curated palette (already guardrail-clean), different row/type steps.
    return {
        "colors": dict(STANDARD_TEMPLATE["colors"]),
        "fonts": dict(STANDARD_TEMPLATE["fonts"]),
        "rowHeight": row_height,
        "sizeStep": size_step,
    }


COMPACT_TEMPLATE: Final[dict[str, Any]] = _standard_variant("compact", "sm")

LARGE_PRINT_TEMPLATE: Final[dict[str, Any]] = _standard_variant("large", "xl")

# The night palette — every guardrail pair curated above the 4.5:1 minimum.
DARK_TEMPLATE: Final[dict[str, Any]] = {
    "colors": {
        "appBackground": "#12161c",
        "panelBackground": "#1a2029",
        "headerBackground": "#0d1b2a",
        "headerText": "#e6edf3",
        "accent": "#58a6ff",
        "rowBackground": "#1a2029",
        "rowAlternateBackground": "#212936",
        "rowText": "#e6edf3",
        "selectedRowBackground": "#2c4f6e",
        "selectedRowText": "#ffffff",
        "groupHeaderBackground": "#161b22",
        "groupHeaderText": "#c9d1d9",
        "statusPositive": "#57ab5a",
        "statusWarning": "#d29922",
        "statusNegative": "#f47067",
    },
    "fonts": {"uiFont": "Inter", "dataFont": "Inter"},
    "rowHeight": "standard",
    "sizeStep": "md",
}

LAUNCH_TEMPLATES: Final[dict[str, dict[str, Any]]] = {
    "standard": STANDARD_TEMPLATE,
    "compact": COMPACT_TEMPLATE,
    "largePrint": LARGE_PRINT_TEMPLATE,
    "dark": DARK_TEMPLATE,
}


def launch_template_options() -> tuple[TemplateOption, ...]:
    """The launch set as picker options — the seed the system set starts from."""
    return tuple(
        TemplateOption(
            template_key=key,
            template_name=LAUNCH_TEMPLATE_NAMES[key],
            document=LAUNCH_TEMPLATES[key],
        )
        for key in LAUNCH_TEMPLATE_KEYS
    )


# --- Wire conversion: persisted record <-> flow document ------------------------------

TEMPLATE_TYPE_USER: Final = TEMPLATE_TYPES[1]

# The persisted font spec needs a weight; the flow document carries families
# only, so saves write the neutral regular weight until a step needs more.
FONT_WEIGHT_REGULAR: Final = 400


def stored_template_option(record: Mapping[str, Any]) -> TemplateOption:
    """One persisted ``colorTemplate`` record as a picker option.

    The persisted document carries no row height (FND-907: row height
    reaches a grid through row themes); launch rows recover theirs from the
    curated set via ``launchSetKey``, everything else renders standard.
    Raises :class:`~mentorapp.ui.theming.ThemingError` when the stored slots
    do not form a complete document — a data error, never rendered quietly.
    """
    launch_key = record.get("launchSetKey")
    row_height: str = (
        LAUNCH_TEMPLATES[launch_key]["rowHeight"]
        if launch_key in LAUNCH_TEMPLATES
        else "standard"
    )
    document: dict[str, Any] = {
        "colors": dict(record["colorSlots"]),
        "fonts": {slot: spec["fontFamily"] for slot, spec in record["fontSlots"].items()},
        "rowHeight": row_height,
        "sizeStep": record["typeStepChoice"],
    }
    validate_template(document)
    return TemplateOption(
        template_key=str(record["colorTemplateID"]),
        template_name=record["colorTemplateName"],
        document=document,
    )


def template_create_payload(finished: FinishedTemplate) -> dict[str, Any]:
    """The TEMPLATE_CREATE body for one finished flow (WTK-114's write shape).

    Font specs take the template's own size step — the flow picks one step
    for the whole template, so both slots render at it until per-slot steps
    become a flow feature.
    """
    document = finished.document
    step: str = document["sizeStep"]
    return {
        "colorTemplateName": finished.template_name,
        "templateType": TEMPLATE_TYPE_USER,
        "colorSlots": dict(document["colors"]),
        "fontSlots": {
            slot: {"stepKey": step, "fontFamily": family, "fontWeight": FONT_WEIGHT_REGULAR}
            for slot, family in document["fonts"].items()
        },
        "typeStepChoice": step,
    }


# --- Never-hide management actions (REQ-044 + the app-wide action rules) --------------

ACTION_SELECT: Final = "select"
ACTION_CREATE_COPY: Final = "createCopy"
ACTION_RENAME: Final = "rename"
ACTION_EDIT_SLOTS: Final = "editSlots"
ACTION_DELETE: Final = "delete"
ACTION_HELP: Final = "help"

# Every action, always listed, for every entry; Help last (app-wide rule).
TEMPLATE_ACTIONS: Final[tuple[str, ...]] = (
    ACTION_SELECT,
    ACTION_CREATE_COPY,
    ACTION_RENAME,
    ACTION_EDIT_SLOTS,
    ACTION_DELETE,
    ACTION_HELP,
)

_OWNER_ONLY_ACTIONS: Final[frozenset[str]] = frozenset(
    {ACTION_RENAME, ACTION_EDIT_SLOTS, ACTION_DELETE}
)


@dataclass(frozen=True)
class ActionDecision:
    """One invocation's answer: runnable, or the educate-voice explanation.

    ``confirmation`` carries the destructive-action wording when the action
    needs one — honest about soft deletion, per the app-wide delete rule.
    """

    action: str
    allowed: bool
    explanation: EducateMessage | None = None
    confirmation: str | None = None


def decide_template_action(action: str, *, origin: str, template_name: str) -> ActionDecision:
    """Decide one action invocation on one picker entry (never hide, educate).

    System templates refuse the owner-only verbs with the WHY and the path
    forward (copy it); they are never hidden or grayed. Unknown actions are
    contract errors — the menu only offers :data:`TEMPLATE_ACTIONS`.
    """
    if action not in TEMPLATE_ACTIONS:
        raise TemplateFlowError(f"'{action}' is not a template action")
    if action in _OWNER_ONLY_ACTIONS and origin == ORIGIN_SYSTEM:
        return ActionDecision(
            action=action,
            allowed=False,
            explanation=EducateMessage(
                what_happened=f"'{template_name}' is a system template, so it can't be "
                f"{'deleted' if action == ACTION_DELETE else 'changed'} here.",
                why="The curated set is shared by everyone and kept readable by the "
                "organization; only your own templates are yours to change.",
                what_next=f"Use Create copy to start your own template from "
                f"'{template_name}', then change or remove your copy freely.",
            ),
        )
    if action == ACTION_DELETE:
        # Destructive: confirm, and say what soft delete actually does.
        return ActionDecision(
            action=action,
            allowed=True,
            confirmation=f"Delete the template '{template_name}'? It is removed from "
            "your template picker and anywhere it is in use returns to your default; "
            "an administrator can restore it.",
        )
    return ActionDecision(action=action, allowed=True)


# --- Slot-filling editor controls (REQ-044/REQ-046) -----------------------------------

CONTROL_COLOR_SLOT: Final = "colorSlot"
CONTROL_FONT_SLOT: Final = "fontSlot"
CONTROL_SIZE_STEP: Final = "sizeStep"
CONTROL_ROW_HEIGHT: Final = "rowHeight"


@dataclass(frozen=True)
class EditorControl:
    """One rendered editor control: what it edits, its current value, its menu.

    ``choices`` is empty for free-value controls (a color well); step
    controls carry the full fixed vocabulary — steps are picked, never
    minted (REQ-046).
    """

    control: str
    slot: str | None
    value: str
    choices: tuple[str, ...] = ()
    label: str = ""


def type_scale_labels() -> dict[str, str]:
    """Step -> display label, each step shown with its px size (REQ-046)."""
    return {step: f"{step} — {size} px" for step, size in TYPE_SCALE_STEPS.items()}


def _scale_controls(row_height: str, size_step: str) -> tuple[EditorControl, ...]:
    labels = type_scale_labels()
    return (
        EditorControl(
            control=CONTROL_SIZE_STEP,
            slot=None,
            value=size_step,
            choices=tuple(TYPE_SCALE_STEPS),
            label=labels[size_step],
        ),
        EditorControl(
            control=CONTROL_ROW_HEIGHT,
            slot=None,
            value=row_height,
            choices=ROW_HEIGHT_STEPS,
        ),
    )


def editor_step_controls(draft: TemplateDraft, step: str) -> tuple[EditorControl, ...]:
    """The controls one slot-filling step renders over the current draft.

    Only the slot-editing steps answer here: ``basis`` renders the copy
    picker and ``review`` renders :func:`~mentorapp.ui.template_flow.review_step`
    cards, so asking either for slot controls is a contract error, not an
    empty step.
    """
    if step in STEP_COLOR_SLOTS:
        colors: Mapping[str, str] = draft.document["colors"]
        return tuple(
            EditorControl(control=CONTROL_COLOR_SLOT, slot=slot, value=colors[slot])
            for slot in STEP_COLOR_SLOTS[step]
        )
    if step == "fonts":
        fonts: Mapping[str, str] = draft.document["fonts"]
        return tuple(
            EditorControl(control=CONTROL_FONT_SLOT, slot=slot, value=fonts[slot])
            for slot in FONT_SLOTS
        )
    if step == "scale":
        return _scale_controls(draft.document["rowHeight"], draft.document["sizeStep"])
    raise TemplateFlowError(f"step '{step}' renders no slot controls")


def apply_control_edit(draft: TemplateDraft, control: EditorControl, value: str) -> None:
    """Commit one control edit through the flow's always-valid mutators.

    Dispatch only — validation and the no-half-applying invariant live in
    ``template_flow``; a bad value rejects loudly and the draft is untouched.
    """
    if control.control == CONTROL_COLOR_SLOT and control.slot is not None:
        set_color_slot(draft, control.slot, value)
    elif control.control == CONTROL_FONT_SLOT and control.slot is not None:
        set_font_slot(draft, control.slot, value)
    elif control.control == CONTROL_SIZE_STEP:
        set_size_step(draft, value)
    elif control.control == CONTROL_ROW_HEIGHT:
        set_row_height(draft, value)
    else:
        raise TemplateFlowError(f"'{control.control}' is not an editor control")


# --- Per-grid row-theme override controls (REQ-018/REQ-044) ----------------------------


@dataclass(frozen=True)
class RowThemeControls:
    """The rendered ``rowTheme`` step: the choice, the scoped controls, the note.

    Controls prefill from the RESOLVED theme — the override editor starts
    from what actually shows through (the user's template under any current
    override), so "customize" begins at the truth, not at a blank.
    """

    choice: str
    controls: tuple[EditorControl, ...]
    scope_note: EducateMessage = ROW_THEME_SCOPE_NOTE
    marks_view_modified: bool = ROW_THEME_MARKS_VIEW_MODIFIED


def row_theme_controls(layers: ThemeLayers) -> RowThemeControls:
    """Render the view-settings row-theme step for one grid's layers.

    Commit stays :func:`~mentorapp.ui.template_flow.build_row_theme` (the
    standard choice is ``None``) and review stays
    :func:`~mentorapp.ui.template_flow.review_row_theme` — this renders the
    controls, scoped to exactly what a row theme may touch.
    """
    effective = resolve_effective_grid_theme(layers)
    choice = ROW_THEME_STANDARD if layers.row_theme is None else ROW_THEME_CUSTOM
    controls = (
        *(
            EditorControl(control=CONTROL_COLOR_SLOT, slot=slot, value=effective.colors[slot])
            for slot in ROW_THEME_COLOR_SLOTS
        ),
        EditorControl(
            control=CONTROL_ROW_HEIGHT,
            slot=None,
            value=effective.row_height,
            choices=ROW_HEIGHT_STEPS,
        ),
        EditorControl(
            control=CONTROL_FONT_SLOT,
            slot=effective.font_slot,
            value=effective.font_slot,
            choices=FONT_SLOTS,
        ),
        EditorControl(
            control=CONTROL_SIZE_STEP,
            slot=None,
            value=effective.size_step,
            choices=tuple(TYPE_SCALE_STEPS),
            label=type_scale_labels()[effective.size_step],
        ),
    )
    log.info(
        "row theme step rendered",
        extra={"context": {"choice": choice, "controls": len(controls)}},
    )
    return RowThemeControls(choice=choice, controls=controls)
