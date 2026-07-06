"""Tests for the SelectOrCreateColorTemplate flow design (WTK-113, REQ-044/REQ-046)."""

from __future__ import annotations

from typing import Any

import pytest

from mentorapp.ui.template_flow import (
    CONTRAST_ACTION_ADJUST,
    CONTRAST_ACTION_SAVE_ANYWAY,
    ORIGIN_SYSTEM,
    ORIGIN_USER,
    ROW_THEME_CHECKED_PAIRS,
    ROW_THEME_STEP,
    SELECTION_APPLIES_INSTANTLY,
    SLOT_FILLING_STEPS,
    STEP_COLOR_SLOTS,
    RowThemeAffordance,
    TemplateFlowError,
    TemplateOption,
    build_row_theme,
    build_template_picker,
    draft_preview,
    finish_user_template,
    review_row_theme,
    review_step,
    select_template,
    set_color_slot,
    set_font_slot,
    set_row_height,
    set_size_step,
    start_user_template,
    template_swatch,
)
from mentorapp.ui.theming import (
    COLOR_SLOTS,
    LAYER_USER_CHOICE,
    ORG_DEFAULT_TEMPLATE_KEY,
    ROW_THEME_COLOR_SLOTS,
    STANDARD_TEMPLATE,
    ThemeLayers,
    ThemingError,
    validate_template,
)
from mentorapp.ui.view_authoring import CREATE_VIEW_WALKTHROUGH


def _document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "colors": dict(STANDARD_TEMPLATE["colors"]),
        "fonts": dict(STANDARD_TEMPLATE["fonts"]),
        "rowHeight": STANDARD_TEMPLATE["rowHeight"],
        "sizeStep": STANDARD_TEMPLATE["sizeStep"],
    }
    document.update(overrides)
    return document


_STANDARD = TemplateOption(ORG_DEFAULT_TEMPLATE_KEY, "Standard", _document())
_DARK = TemplateOption("dark", "Dark", _document())
_MINE = TemplateOption("mine", "My template", _document())


# --- Picker: ordering, swatches, instant selection (REQ-044) -------------------------


def test_picker_leads_with_org_default_then_system_then_user() -> None:
    picker = build_template_picker(system_templates=[_DARK, _STANDARD], user_templates=[_MINE])
    assert [entry.template_key for entry in picker.entries] == [
        ORG_DEFAULT_TEMPLATE_KEY,
        "dark",
        "mine",
    ]
    assert [entry.origin for entry in picker.entries] == [
        ORIGIN_SYSTEM,
        ORIGIN_SYSTEM,
        ORIGIN_USER,
    ]
    # No stored choice: the org default is what renders and is marked active.
    assert [entry.active for entry in picker.entries] == [True, False, False]


def test_picker_requires_the_org_default_reset_target() -> None:
    with pytest.raises(TemplateFlowError, match="org default"):
        build_template_picker(system_templates=[_DARK], user_templates=[])


def test_swatch_is_drawn_from_the_templates_own_slots() -> None:
    swatch = template_swatch(_document())
    assert swatch.row_text == STANDARD_TEMPLATE["colors"]["rowText"]
    assert (
        swatch.row_alternate_background
        == (STANDARD_TEMPLATE["colors"]["rowAlternateBackground"])
    )
    assert swatch.data_font == STANDARD_TEMPLATE["fonts"]["dataFont"]
    assert swatch.size_step == STANDARD_TEMPLATE["sizeStep"]


def test_selection_is_instant_and_org_default_clears_the_choice() -> None:
    assert SELECTION_APPLIES_INSTANTLY
    picker = build_template_picker(
        system_templates=[_STANDARD, _DARK], user_templates=[_MINE], active_key="mine"
    )
    chosen = select_template(picker, "dark")
    assert chosen.template_key == "dark"
    assert chosen.as_preference_value() == {"templateKey": "dark"}
    # Picking the org default clears layer two — provenance stays orgDefault.
    reset = select_template(picker, ORG_DEFAULT_TEMPLATE_KEY)
    assert reset.template_key is None
    assert reset.as_preference_value() is None
    with pytest.raises(TemplateFlowError, match="not in the picker"):
        select_template(picker, "someoneElses")


# --- Create flow: complete copy, valid at every step (REQ-044) -----------------------


def test_color_steps_partition_the_slot_vocabulary() -> None:
    assert SLOT_FILLING_STEPS[0] == "basis"
    assert SLOT_FILLING_STEPS[-1] == "review"
    stepped = [slot for slots in STEP_COLOR_SLOTS.values() for slot in slots]
    assert sorted(stepped) == sorted(COLOR_SLOTS)
    assert len(stepped) == len(set(stepped))
    assert set(STEP_COLOR_SLOTS) < set(SLOT_FILLING_STEPS)


def test_draft_is_a_copy_and_stays_valid_through_mutations() -> None:
    draft = start_user_template(_DARK)
    set_color_slot(draft, "rowBackground", "#111418")
    set_font_slot(draft, "dataFont", "Roboto Mono")
    set_size_step(draft, "lg")
    set_row_height(draft, "compact")
    validate_template(draft.document)
    assert draft.basis_key == "dark"
    # The basis document is untouched — a copy, never a shared reference.
    assert _DARK.document["colors"]["rowBackground"] != "#111418"
    preview = draft_preview(draft)
    assert preview.colors["rowBackground"] == "#111418"
    assert set(preview.provenance.values()) == {LAYER_USER_CHOICE}


def test_bad_edits_reject_loudly_and_leave_the_draft_untouched() -> None:
    draft = start_user_template(_STANDARD)
    before = dict(draft.document["colors"])
    with pytest.raises(ThemingError, match="every fixed color slot"):
        set_color_slot(draft, "myFavoriteButton", "#ff0000")
    with pytest.raises(ThemingError, match="#rrggbb"):
        set_color_slot(draft, "rowText", "red")
    with pytest.raises(ThemingError, match="type-scale step"):
        set_size_step(draft, "13pt")
    with pytest.raises(ThemingError, match="rowHeight"):
        set_row_height(draft, "cozy")
    assert draft.document["colors"] == before


# --- Review: warning cards with previews, save never blocked (REQ-046) ---------------


def test_review_presents_cards_and_save_stays_enabled() -> None:
    draft = start_user_template(_STANDARD)
    set_color_slot(draft, "rowText", "#eeeeee")  # near-invisible on white rows
    review = review_step(draft)
    assert review.save_enabled
    assert review.cards
    card = review.cards[0]
    assert card.warning.text_slot == "rowText"
    assert card.warning.preview.text_color == "#eeeeee"
    assert "below the 4.5:1" in card.ratio_label
    assert card.adjust_step == "rowColors"
    assert card.actions == (CONTRAST_ACTION_ADJUST, CONTRAST_ACTION_SAVE_ANYWAY)


def test_finish_requires_a_name_and_carries_the_guardrail_outcome() -> None:
    draft = start_user_template(_STANDARD)
    set_color_slot(draft, "rowText", "#eeeeee")
    with pytest.raises(TemplateFlowError, match="needs a name"):
        finish_user_template(draft, name="   ")
    finished = finish_user_template(draft, name="  My readable theme ")
    assert finished.template_name == "My readable theme"
    assert finished.warnings  # saved through, recorded — never re-asked
    validate_template(finished.document)
    assert finished.document is not draft.document


# --- The per-grid row-theme affordance (REQ-018/REQ-044) -----------------------------


def test_affordance_lives_in_the_view_walkthrough_with_row_scope_only() -> None:
    assert ROW_THEME_STEP in CREATE_VIEW_WALKTHROUGH
    affordance = RowThemeAffordance()
    assert affordance.choices == ("standard", "custom")
    assert affordance.color_slots == ROW_THEME_COLOR_SLOTS
    assert "template" in affordance.scope_note.what_next


def test_build_row_theme_returns_none_for_standard_and_rejects_chrome() -> None:
    assert build_row_theme() is None
    with pytest.raises(ThemingError, match="rows only"):
        build_row_theme(colors={"headerBackground": "#000000"})
    document = build_row_theme(
        colors={"rowBackground": "#111418"},
        row_height="compact",
        font={"fontSlot": "dataFont", "sizeStep": "sm"},
    )
    assert document == {
        "colors": {"rowBackground": "#111418"},
        "rowHeight": "compact",
        "font": {"fontSlot": "dataFont", "sizeStep": "sm"},
    }


def test_row_theme_review_catches_the_cross_layer_clash() -> None:
    # The override alone looks fine; over the template's white rows it clashes.
    layers = ThemeLayers(
        org_default=_document(),
        row_theme={"colors": {"rowText": "#f5f5f5"}},
    )
    cards = review_row_theme(layers)
    pairs = {(card.warning.text_slot, card.warning.background_slot) for card in cards}
    assert ("rowText", "rowBackground") in pairs
    assert pairs <= set(ROW_THEME_CHECKED_PAIRS)
    # The chrome pair is template-save business, never a row-theme card.
    assert all(card.warning.text_slot != "headerText" for card in cards)
    assert review_row_theme(ThemeLayers(org_default=_document())) == ()
