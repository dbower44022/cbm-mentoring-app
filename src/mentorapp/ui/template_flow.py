"""SelectOrCreateColorTemplate flow (WTK-113): the REQ-044/REQ-046 template UX.

The flow-level design between WTK-112's semantics (``ui.theming`` — the slot
structure, precedence, and guardrail this module composes and never
re-decides) and the surfaces that render it (WTK-118 guardrail UI, WTK-119
template management):

- **Entry & picker:** templates live where every per-user preference lives —
  the user menu (:data:`TEMPLATE_PICKER_ENTRY`, layout standard). The picker
  lists the org default first, the rest of the system set, then the user's
  own templates; every entry carries a :class:`TemplateSwatch` drawn from its
  OWN slots, so templates are judged by eye, not by name. Selection applies
  instantly app-wide (:data:`SELECTION_APPLIES_INSTANTLY` — view-selector
  parity, REQ-017) and persists as the layer-two choice. Picking the org
  default CLEARS the choice rather than pinning a copy of it
  (:func:`select_template`): the user asked for "the organization's
  default", provenance stays ``orgDefault``, and a later rebrand follows
  them automatically.
- **Create = copy, never blank:** a user template starts as a copy of an
  existing template (:func:`start_user_template` — the look & feel standard's
  "Dark, the usual base for personal copies"). Because ``validate_template``
  demands completeness, copying is what keeps the invariant every mutator
  preserves: a :class:`TemplateDraft` is a COMPLETE, VALID document at every
  step, so the live preview (:func:`draft_preview`) always renders and the
  save verb is never structurally impossible.
- **Slot-filling steps:** :data:`SLOT_FILLING_STEPS` fixes the walkthrough
  order (basis → chrome → rows → status → fonts → scale → review); the three
  color steps partition :data:`~mentorapp.ui.theming.COLOR_SLOTS` exactly, so
  every slot has one home and the review's Adjust action always has a step to
  jump to. Steps are freely revisitable — this is an editor inside the
  standard form rules (dirty guard on leave), not a workprocess.
- **Contrast warning-with-preview presentation:** the review step runs
  ``review_user_template`` and renders each warning as a
  :class:`ContrastWarningCard` — the WTK-112 preview (sample text in the
  actual combination), the educate message, a plain ratio label, and exactly
  two actions: Adjust (jumps to the failing pair's step) or Save anyway.
  Save stays enabled no matter how many cards there are
  (``GUARDRAIL_NEVER_BLOCKS``) — the shared presentation WTK-118 implements.
- **Row-theme override affordance:** per-grid theming is offered ONLY inside
  the view-settings form's ``rowTheme`` walkthrough step (WTK-049) — never in
  the template picker, because a row theme is a view setting and marks the
  view modified like any other (:data:`ROW_THEME_MARKS_VIEW_MODIFIED`). The
  choice is standard (``None`` — the resolved template shows through) or a
  custom editor scoped to exactly what ``validate_row_theme`` allows; the
  scope note explains WHY chrome slots aren't offered instead of hiding the
  concept. Its guardrail pass (:func:`review_row_theme`) checks the RESOLVED
  combination — the override over the user's actual template — so a
  cross-layer clash warns even when each layer looks fine alone.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from mentorapp.observability import get_logger
from mentorapp.ui.auth_flows import EducateMessage
from mentorapp.ui.theming import (
    CHROME_COLOR_SLOTS as _CHROME_SLOTS,
)
from mentorapp.ui.theming import (
    CONTRAST_CHECKED_PAIRS,
    FONT_SLOTS,
    GUARDRAIL_NEVER_BLOCKS,
    ORG_DEFAULT_TEMPLATE_KEY,
    ROW_HEIGHT_STEPS,
    ROW_THEME_COLOR_SLOTS,
    STANDARD_TEMPLATE,
    STATUS_COLOR_SLOTS,
    TYPE_SCALE_STEPS,
    ContrastWarning,
    EffectiveGridTheme,
    ThemeLayers,
    check_template_contrast,
    resolve_effective_grid_theme,
    review_user_template,
    validate_row_theme,
    validate_template,
)

log = get_logger(__name__)


class TemplateFlowError(ValueError):
    """A flow input rejected before any state changes, with the reason."""


# --- Entry & picker (REQ-044: pick a template, wholesale) ----------------------------

# The user menu is the address for every per-user preference (layout standard).
TEMPLATE_PICKER_ENTRY: Final = "userMenu.themes"

ORIGIN_SYSTEM: Final = "system"
ORIGIN_USER: Final = "user"

# Selection is the view selector's contract (REQ-017): applies the moment it
# is picked, everywhere at once — no apply button, no per-panel staging.
SELECTION_APPLIES_INSTANTLY: Final = True

# Where the layer-two choice lives: the ONE preference mechanism (REQ-060 —
# a new feature needs a new preferenceKey, never a new table or endpoint).
# :meth:`TemplateSelection.as_preference_value` is the document persisted
# under this key; the constant lives HERE, beside that payload, so the writer
# (the picker) and every reader (GET /theming/effective) name the same row.
TEMPLATE_CHOICE_PREFERENCE_KEY: Final = "theming.templateChoice"


@dataclass(frozen=True)
class TemplateOption:
    """One listable template: its key, display name, and full document."""

    template_key: str
    template_name: str
    document: Mapping[str, Any]


@dataclass(frozen=True)
class TemplateSwatch:
    """The picker's per-entry preview: enough of the template's OWN slots to judge.

    Sample text renders in the template's data font at its size step over the
    banded row pair — the same surfaces the contrast guardrail watches, so
    what sells a template in the picker is what it actually looks like.
    """

    header_background: str
    header_text: str
    row_background: str
    row_alternate_background: str
    row_text: str
    accent: str
    data_font: str
    size_step: str
    sample_text: str = "Aa 0123456789"


def template_swatch(document: Mapping[str, Any]) -> TemplateSwatch:
    """The swatch for one validated template document."""
    validate_template(document)
    colors: Mapping[str, str] = document["colors"]
    return TemplateSwatch(
        header_background=colors["headerBackground"],
        header_text=colors["headerText"],
        row_background=colors["rowBackground"],
        row_alternate_background=colors["rowAlternateBackground"],
        row_text=colors["rowText"],
        accent=colors["accent"],
        data_font=document["fonts"]["dataFont"],
        size_step=document["sizeStep"],
    )


@dataclass(frozen=True)
class PickerEntry:
    """One picker row: identity, origin badge, live swatch, active marker."""

    template_key: str
    template_name: str
    origin: str
    swatch: TemplateSwatch
    active: bool


@dataclass(frozen=True)
class TemplatePicker:
    """The rendered picker: org default first, system set, then the user's own.

    ``active_key`` ``None`` means no layer-two choice is stored — the org
    default entry is what renders and is marked active.
    """

    entries: tuple[PickerEntry, ...]
    active_key: str | None
    org_default_key: str


def build_template_picker(
    *,
    system_templates: Sequence[TemplateOption],
    user_templates: Sequence[TemplateOption],
    active_key: str | None = None,
    org_default_key: str = ORG_DEFAULT_TEMPLATE_KEY,
) -> TemplatePicker:
    """Order and mark the picker's entries from the stored template sets.

    The org default leads regardless of listing order — it is every user's
    starting point and the reset target. Raises :class:`TemplateFlowError`
    when the org default is missing from the system set: a picker without a
    reset target cannot honor the layering.
    """
    ordered = sorted(
        system_templates, key=lambda option: option.template_key != org_default_key
    )
    if not ordered or ordered[0].template_key != org_default_key:
        raise TemplateFlowError(
            f"the system set must include the org default '{org_default_key}'"
        )
    entries = tuple(
        PickerEntry(
            template_key=option.template_key,
            template_name=option.template_name,
            origin=origin,
            swatch=template_swatch(option.document),
            active=option.template_key == (active_key or org_default_key),
        )
        for origin, options in ((ORIGIN_SYSTEM, ordered), (ORIGIN_USER, user_templates))
        for option in options
    )
    return TemplatePicker(
        entries=entries, active_key=active_key, org_default_key=org_default_key
    )


@dataclass(frozen=True)
class TemplateSelection:
    """The decision the picker commits — instant, app-wide, persisted.

    ``template_key`` ``None`` is the cleared choice: layer one (the org
    default) shows through with honest ``orgDefault`` provenance.
    """

    template_key: str | None

    def as_preference_value(self) -> dict[str, str] | None:
        """The ``userPreference`` payload WTK-116 persists; ``None`` clears the row."""
        return None if self.template_key is None else {"templateKey": self.template_key}


def select_template(picker: TemplatePicker, template_key: str) -> TemplateSelection:
    """Resolve one picker click into the layer-two choice (REQ-044).

    Picking the org default deliberately CLEARS the stored choice instead of
    pinning a copy: the user asked for the organization's default, and when
    the organization rebrands, they follow it. Unknown keys are contract
    errors — the picker only offers what it listed.
    """
    if template_key not in {entry.template_key for entry in picker.entries}:
        raise TemplateFlowError(f"template '{template_key}' is not in the picker")
    if template_key == picker.org_default_key:
        return TemplateSelection(template_key=None)
    return TemplateSelection(template_key=template_key)


# --- The create flow: slot-filling over a complete copy (REQ-044/REQ-046) -----------

# The fixed walkthrough order; steps are revisitable (an editor, not a wizard).
SLOT_FILLING_STEPS: Final[tuple[str, ...]] = (
    "basis",
    "chromeColors",
    "rowColors",
    "statusColors",
    "fonts",
    "scale",
    "review",
)

# Which step edits which color slots — a partition of COLOR_SLOTS, so every
# slot has exactly one home and Adjust always has a step to jump to.
STEP_COLOR_SLOTS: Final[dict[str, tuple[str, ...]]] = {
    "chromeColors": _CHROME_SLOTS,
    "rowColors": ROW_THEME_COLOR_SLOTS,
    "statusColors": STATUS_COLOR_SLOTS,
}

STEP_FOR_SLOT: Final[dict[str, str]] = {
    slot: step for step, slots in STEP_COLOR_SLOTS.items() for slot in slots
}


@dataclass
class TemplateDraft:
    """A user template mid-flow: ALWAYS a complete, valid document.

    Every mutator validates a candidate copy before committing it, so a bad
    edit fails loudly and leaves the draft untouched — the live preview never
    renders a half-applied state (WTK-112's no-half-applying stance).
    """

    basis_key: str
    document: dict[str, Any]


def _copy_document(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "colors": dict(document["colors"]),
        "fonts": dict(document["fonts"]),
        "rowHeight": document["rowHeight"],
        "sizeStep": document["sizeStep"],
    }


def start_user_template(basis: TemplateOption) -> TemplateDraft:
    """Begin the create flow as a COPY of an existing template.

    Never blank-slate: completeness is a template invariant, and starting
    from a curated complete document is what makes every step previewable
    and the save always structurally possible.
    """
    validate_template(basis.document)
    return TemplateDraft(basis_key=basis.template_key, document=_copy_document(basis.document))


def _commit(draft: TemplateDraft, candidate: dict[str, Any]) -> None:
    validate_template(candidate)
    draft.document = candidate


def set_color_slot(draft: TemplateDraft, slot: str, value: str) -> None:
    """Fill one color slot; an unknown slot or non-hex value rejects loudly."""
    candidate = _copy_document(draft.document)
    candidate["colors"][slot] = value
    _commit(draft, candidate)


def set_font_slot(draft: TemplateDraft, slot: str, family: str) -> None:
    """Fill one font slot from the fixed font-slot vocabulary."""
    candidate = _copy_document(draft.document)
    candidate["fonts"][slot] = family
    _commit(draft, candidate)


def set_size_step(draft: TemplateDraft, step: str) -> None:
    """Pick the template's type-scale STEP — never an arbitrary point size."""
    candidate = _copy_document(draft.document)
    candidate["sizeStep"] = step
    _commit(draft, candidate)


def set_row_height(draft: TemplateDraft, step: str) -> None:
    """Pick the template's row-height step from the fixed vocabulary."""
    candidate = _copy_document(draft.document)
    candidate["rowHeight"] = step
    _commit(draft, candidate)


def draft_preview(draft: TemplateDraft) -> EffectiveGridTheme:
    """The live preview beside every step: the draft AS the app-wide choice.

    Rendered through the one resolver so the preview and the eventual truth
    can never disagree; provenance reads ``userChoice`` throughout because a
    chosen template replaces the default wholesale.
    """
    return resolve_effective_grid_theme(
        ThemeLayers(org_default=STANDARD_TEMPLATE, user_choice=draft.document)
    )


# --- Contrast warning-with-preview presentation (REQ-046) ----------------------------

CONTRAST_ACTION_ADJUST: Final = "adjustSlots"
CONTRAST_ACTION_SAVE_ANYWAY: Final = "saveAnyway"
CONTRAST_WARNING_ACTIONS: Final[tuple[str, ...]] = (
    CONTRAST_ACTION_ADJUST,
    CONTRAST_ACTION_SAVE_ANYWAY,
)


@dataclass(frozen=True)
class ContrastWarningCard:
    """One warning as the UI renders it: preview, educate message, two actions.

    ``adjust_step`` is where Adjust jumps — the slot-filling step that edits
    the failing pair's text slot. The card never carries a disable: Save
    anyway is always live (``GUARDRAIL_NEVER_BLOCKS``).
    """

    warning: ContrastWarning
    ratio_label: str
    adjust_step: str
    actions: tuple[str, ...] = CONTRAST_WARNING_ACTIONS


def contrast_warning_card(warning: ContrastWarning) -> ContrastWarningCard:
    """Present one WTK-112 warning as the card every guardrail surface renders.

    The one card builder (WTK-118 composes it for the save-time pass):
    inputs are a :class:`~mentorapp.ui.theming.ContrastWarning`; the card
    carries the plain ratio label and the Adjust jump target, and cannot
    fail — every checked slot has exactly one slot-filling step.
    """
    return ContrastWarningCard(
        warning=warning,
        ratio_label=(
            f"{warning.ratio:.1f}:1 — below the {warning.minimum:g}:1 readability minimum"
        ),
        adjust_step=STEP_FOR_SLOT[warning.text_slot],
    )


@dataclass(frozen=True)
class TemplateReview:
    """The review step's rendering: the cards, and a save that stays enabled."""

    cards: tuple[ContrastWarningCard, ...]
    save_enabled: bool


def review_step(draft: TemplateDraft) -> TemplateReview:
    """Run the WTK-112 guardrail over the draft and present it (REQ-046).

    Warnings become cards — seen, not just counted — and ``save_enabled`` is
    the guardrail's non-negotiable, restated where the button binds to it.
    """
    warnings = review_user_template(draft.document)
    return TemplateReview(
        cards=tuple(contrast_warning_card(warning) for warning in warnings),
        save_enabled=GUARDRAIL_NEVER_BLOCKS,
    )


@dataclass(frozen=True)
class FinishedTemplate:
    """What the flow hands WTK-119 to persist: name, document, shown warnings.

    Carrying the warnings the user saved through is deliberate — the persist
    step records that the guardrail ran and was overridden, never re-asks.
    """

    template_name: str
    document: dict[str, Any]
    warnings: tuple[ContrastWarning, ...]


def finish_user_template(draft: TemplateDraft, *, name: str) -> FinishedTemplate:
    """Commit the flow: a named, validated document plus its guardrail outcome."""
    template_name = name.strip()
    if not template_name:
        raise TemplateFlowError("a template needs a name before it can be saved")
    warnings = review_user_template(draft.document)
    log.info(
        "user template finished",
        extra={
            "context": {
                "basisKey": draft.basis_key,
                "templateName": template_name,
                "contrastWarnings": len(warnings),
            }
        },
    )
    return FinishedTemplate(
        template_name=template_name,
        document=_copy_document(draft.document),
        warnings=warnings,
    )


# --- The per-grid row-theme override affordance (REQ-018/REQ-044) --------------------

# The affordance lives in the view-settings form's walkthrough step of this
# name (view_authoring.CREATE_VIEW_WALKTHROUGH) — never in the template picker.
ROW_THEME_STEP: Final = "rowTheme"

ROW_THEME_STANDARD: Final = "standard"
ROW_THEME_CUSTOM: Final = "custom"
ROW_THEME_CHOICES: Final[tuple[str, ...]] = (ROW_THEME_STANDARD, ROW_THEME_CUSTOM)

# A row theme is a view setting: touching it marks the view modified exactly
# like a column or sort change (selector indicator, saveable as a user view).
ROW_THEME_MARKS_VIEW_MODIFIED: Final = True

# Why the editor offers only row-scoped slots — shown at the affordance, so
# the boundary is taught instead of silently enforced (educate, never hide).
ROW_THEME_SCOPE_NOTE: Final = EducateMessage(
    what_happened="This view can restyle its own rows only.",
    why="App chrome — the header, panels, and accent — comes from your color "
    "template, so every grid stays recognizably one app.",
    what_next="Pick row colors, row height, and data-font size here; to change "
    "the app's overall look, choose or create a template under Themes.",
)


@dataclass(frozen=True)
class RowThemeAffordance:
    """What the ``rowTheme`` step renders: the choices and the scoped vocabulary."""

    choices: tuple[str, ...] = ROW_THEME_CHOICES
    color_slots: tuple[str, ...] = ROW_THEME_COLOR_SLOTS
    row_height_steps: tuple[str, ...] = ROW_HEIGHT_STEPS
    font_slots: tuple[str, ...] = FONT_SLOTS
    size_steps: tuple[str, ...] = tuple(TYPE_SCALE_STEPS)
    scope_note: EducateMessage = ROW_THEME_SCOPE_NOTE


def build_row_theme(
    *,
    colors: Mapping[str, str] | None = None,
    row_height: str | None = None,
    font: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """The custom choice's commit: a validated override document, or ``None``.

    Everything unset means the standard choice — ``gridView.rowTheme`` stays
    ``None`` and the resolved template shows through (WTK-041's null
    contract). Scope violations reject through ``validate_row_theme``.
    """
    document: dict[str, Any] = {}
    if colors:
        document["colors"] = dict(colors)
    if row_height is not None:
        document["rowHeight"] = row_height
    if font:
        document["font"] = dict(font)
    if not document:
        return None
    validate_row_theme(document)
    return document


# The pairs a row theme can influence — the header pair is chrome, checked at
# template save, and would only repeat noise here.
ROW_THEME_CHECKED_PAIRS: Final[tuple[tuple[str, str], ...]] = tuple(
    pair for pair in CONTRAST_CHECKED_PAIRS if set(pair) <= set(ROW_THEME_COLOR_SLOTS)
)


def review_row_theme(layers: ThemeLayers) -> tuple[ContrastWarningCard, ...]:
    """The guardrail at the row-theme editor: check the RESOLVED combination.

    A row theme rarely names both halves of a pair, so checking it alone
    would miss the real risk — its color over the color showing through from
    the user's template. Resolving first catches the cross-layer clash;
    warnings present as the same cards, and nothing here blocks either.
    """
    effective = resolve_effective_grid_theme(layers)
    return tuple(
        contrast_warning_card(warning)
        for warning in check_template_contrast(effective.colors)
        if (warning.text_slot, warning.background_slot) in ROW_THEME_CHECKED_PAIRS
    )
