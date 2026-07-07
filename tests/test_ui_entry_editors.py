"""Entry sizing & rich-text control design gate: the prototype-gate delta (WTK-204)."""

from __future__ import annotations

import pytest

from mentorapp.storage import (
    Engagement,
    MentoringSession,
    ProgressGoal,
    built_in_fields,
)
from mentorapp.ui.entry_editors import (
    MIN_EDITOR_HEIGHT,
    PREP_ACTION_ITEMS_EDITOR,
    PREP_ENTRY_EDITORS,
    PREP_NOTES_EDITOR,
    RICH_TEXT_CONTROL,
    RICH_TEXT_FIELD_TYPE,
    EntryEditor,
    fill_entry_layout,
    is_rich_text,
)
from mentorapp.ui.field_edit_window import window_control

# --- The rich-text control declaration (REQ-090) -----------------------------------


def test_control_is_registry_driven_like_the_lookup_control() -> None:
    assert RICH_TEXT_CONTROL.field_type == RICH_TEXT_FIELD_TYPE == "richText"
    assert is_rich_text("richText")
    # Plain text stays a plain control — no heuristic promotion.
    assert not is_rich_text("text")


def test_control_offers_full_editing_and_produces_clean_html() -> None:
    # REQ-090 acceptance: structure, lists, links, undo/redo; clean HTML out.
    for capability in ("bold", "bulletedList", "numberedList", "link", "undo", "redo"):
        assert capability in RICH_TEXT_CONTROL.capabilities
    assert RICH_TEXT_CONTROL.value_format == "html"


def test_paste_fidelity_covers_word_and_email() -> None:
    # REQ-090 acceptance: pasting from Word and from an email client keeps
    # formatting, lists, and links.
    assert "word" in RICH_TEXT_CONTROL.paste_sources
    assert "email" in RICH_TEXT_CONTROL.paste_sources


def test_component_selection_is_recorded() -> None:
    # Design-time selection under the boring-dependency policy; the contract
    # above stays component-agnostic so a licensing re-ruling swaps this
    # value without changing the design.
    assert RICH_TEXT_CONTROL.component == "ckeditor5"


def test_every_rich_text_entry_point_gets_the_same_control() -> None:
    # "The same control appears at every rich-text entry point" — both prep
    # editors carry the one control, not per-form widget choices.
    assert PREP_NOTES_EDITOR.control == RICH_TEXT_CONTROL
    assert PREP_ACTION_ITEMS_EDITOR.control == RICH_TEXT_CONTROL


def test_narrative_columns_route_to_the_one_control_through_the_registry() -> None:
    # WTK-205 closes the design's deferred wiring: the mentoring narrative
    # columns carry the registry type, so every entry point resolves them to
    # RICH_TEXT_CONTROL by type alone — no UI-side field-name list exists.
    # (PI-010 reconciled the narrative set: the session's notes/action items
    # and the engagement summary replaced the meetingNote/nextStep columns.)
    narrative_names = {
        "engagementSummary",
        "sessionNotes",
        "actionItems",
        "progressGoalDescription",
    }
    specs = [
        spec
        for spec in built_in_fields([Engagement, MentoringSession, ProgressGoal])
        if spec.field_name in narrative_names
    ]
    assert {spec.field_name for spec in specs} == narrative_names
    for spec in specs:
        assert is_rich_text(spec.field_type)
        assert window_control(spec.field_type) is RICH_TEXT_CONTROL


# --- Fill-the-panel sizing (REQ-089) ------------------------------------------------


def test_allocation_consumes_every_available_pixel() -> None:
    # The no-white-space rule applied to data entry: whatever the panel
    # offers beyond fixed chrome is fully allocated.
    for available in (400, 641, 900, 1440):
        layout = fill_entry_layout(available, PREP_ENTRY_EDITORS, fixed_chrome_px=120)
        assert sum(layout.allocations.values()) == available - 120
        assert not layout.panel_scrolls


def test_enlarging_the_panel_enlarges_the_editors() -> None:
    # REQ-089 acceptance: enlarging the panel (splitter or window) visibly
    # enlarges the entry areas — recomputed, never a fixed height.
    small = fill_entry_layout(500, PREP_ENTRY_EDITORS)
    large = fill_entry_layout(900, PREP_ENTRY_EDITORS)
    for key in ("sessionNotes", "actionItems"):
        assert large.height_of(key) > small.height_of(key)


def test_prep_surface_splits_notes_to_action_items_three_to_two() -> None:
    # The approved prototype ratio (prototype/styles.css screen C).
    layout = fill_entry_layout(1000, PREP_ENTRY_EDITORS)
    assert layout.height_of("sessionNotes") == 600
    assert layout.height_of("actionItems") == 400


def test_fixed_chrome_never_absorbs_fill_space() -> None:
    bare = fill_entry_layout(800, PREP_ENTRY_EDITORS, fixed_chrome_px=0)
    chromed = fill_entry_layout(1000, PREP_ENTRY_EDITORS, fixed_chrome_px=200)
    assert bare.allocations == chromed.allocations


def test_floors_hold_and_the_panel_scrolls_when_space_runs_out() -> None:
    # Below the floors the editors stay readable and the panel scrolls —
    # never an unreadable squash, never a silent clip.
    layout = fill_entry_layout(100, PREP_ENTRY_EDITORS)
    assert layout.panel_scrolls
    assert layout.height_of("sessionNotes") == MIN_EDITOR_HEIGHT
    assert layout.height_of("actionItems") == MIN_EDITOR_HEIGHT


def test_floor_pins_one_editor_while_the_rest_keep_filling() -> None:
    # Waterfill: a heavily outweighed editor pins at its floor; every other
    # pixel still lands in the remaining editors (no white space appears).
    editors = (
        EntryEditor(key="big", label="Big", fill_weight=9),
        EntryEditor(key="small", label="Small", fill_weight=1, min_height_px=90),
    )
    layout = fill_entry_layout(600, editors)
    assert layout.height_of("small") == 90
    assert layout.height_of("big") == 510
    assert not layout.panel_scrolls


def test_rounding_never_drops_a_pixel() -> None:
    # 3:2 over odd totals cannot split evenly; the remainder is handed out,
    # not leaked as white space.
    for available in (503, 997, 1001):
        layout = fill_entry_layout(available, PREP_ENTRY_EDITORS)
        assert sum(layout.allocations.values()) == available


def test_flex_rule_is_the_prototype_shape() -> None:
    assert PREP_NOTES_EDITOR.flex_rule() == "flex: 3 1 0; min-height: 90px"
    assert PREP_ACTION_ITEMS_EDITOR.flex_rule() == "flex: 2 1 0; min-height: 90px"


def test_declaration_bugs_are_reported_loudly() -> None:
    with pytest.raises(ValueError, match="no entry editors"):
        fill_entry_layout(500, ())
    with pytest.raises(ValueError, match="duplicate"):
        fill_entry_layout(500, (PREP_NOTES_EDITOR, PREP_NOTES_EDITOR))
    with pytest.raises(ValueError, match="non-negative"):
        fill_entry_layout(-1, PREP_ENTRY_EDITORS)
    with pytest.raises(ValueError, match="fill_weight"):
        EntryEditor(key="bad", label="Bad", fill_weight=0)
