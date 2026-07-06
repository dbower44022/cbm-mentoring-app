"""WTK-118 — the contrast guardrail pass: warn with a preview, never block.

REQ-046 (readability warnings on user-template saves) plus the REQ-092
banding-subtlety check shown alongside, and the ``meta.contrastWarnings``
wire shape that carries the previews.
"""

from __future__ import annotations

from mentorapp.ui.contrast_guardrail import (
    KIND_BANDING,
    KIND_READABILITY,
    BandingWarningCard,
    GuardrailReview,
    guardrail_warning_entries,
    run_template_guardrail,
)
from mentorapp.ui.row_banding import BANDING_DISTINCTION_FLOOR, BANDING_SUBTLETY_CEILING
from mentorapp.ui.template_flow import (
    CONTRAST_ACTION_ADJUST,
    CONTRAST_ACTION_SAVE_ANYWAY,
    ContrastWarningCard,
)
from mentorapp.ui.theming import CONTRAST_MINIMUM, STANDARD_TEMPLATE


def curated_colors() -> dict[str, str]:
    return dict(STANDARD_TEMPLATE["colors"])


# --- Detection: both checks, one pass -------------------------------------------------


def test_curated_colors_pass_clean() -> None:
    review = run_template_guardrail(curated_colors())
    assert review == GuardrailReview(
        readability_cards=(), banding_cards=(), save_enabled=True
    )


def test_unreadable_pair_yields_a_readability_card() -> None:
    colors = curated_colors() | {"rowText": "#c9ced4"}  # light grey on white
    review = run_template_guardrail(colors)
    pairs = {
        (card.warning.text_slot, card.warning.background_slot)
        for card in review.readability_cards
    }
    assert ("rowText", "rowBackground") in pairs
    for card in review.readability_cards:
        assert isinstance(card, ContrastWarningCard)
        assert card.warning.ratio < CONTRAST_MINIMUM


def test_pronounced_banding_warns_alongside_readability() -> None:
    # Black alternate on white base: banding reads as striping (REQ-092), and
    # the dark rowText becomes unreadable on the dark alternate rows.
    colors = curated_colors() | {"rowAlternateBackground": "#111111"}
    review = run_template_guardrail(colors)
    assert len(review.banding_cards) == 1
    card = review.banding_cards[0]
    assert card.warning.ratio > BANDING_SUBTLETY_CEILING
    assert "subtlety ceiling" in card.ratio_label
    assert any(
        (c.warning.text_slot, c.warning.background_slot)
        == ("rowText", "rowAlternateBackground")
        for c in review.readability_cards
    )


def test_invisible_banding_warns_from_the_distinction_floor() -> None:
    colors = curated_colors()
    colors["rowAlternateBackground"] = colors["rowBackground"]
    review = run_template_guardrail(colors)
    assert len(review.banding_cards) == 1
    assert review.banding_cards[0].warning.ratio <= BANDING_DISTINCTION_FLOOR
    assert "distinction floor" in review.banding_cards[0].ratio_label


# --- Warn WITH a preview ---------------------------------------------------------------


def test_readability_card_previews_the_actual_combination() -> None:
    colors = curated_colors() | {"headerText": "#3a4a66"}  # dark on dark header
    review = run_template_guardrail(colors)
    card = next(
        c for c in review.readability_cards if c.warning.text_slot == "headerText"
    )
    assert card.warning.preview.text_color == "#3a4a66"
    assert card.warning.preview.background_color == colors["headerBackground"]
    assert card.adjust_step == "chromeColors"  # Adjust jumps to the failing pair's step


def test_banding_card_previews_sample_rows_in_the_actual_pair() -> None:
    colors = curated_colors() | {"rowAlternateBackground": "#111111"}
    card = run_template_guardrail(colors).banding_cards[0]
    assert isinstance(card, BandingWarningCard)
    assert card.preview.base_background == colors["rowBackground"]
    assert card.preview.alternate_background == "#111111"
    assert card.preview.text_color == colors["rowText"]
    assert card.adjust_step == "rowColors"


# --- Never block ------------------------------------------------------------------------


def test_save_stays_enabled_no_matter_how_bad_it_gets() -> None:
    colors = {slot: "#808080" for slot in curated_colors()}
    review = run_template_guardrail(colors)
    assert len(review.readability_cards) == 5  # every checked pair fails
    assert len(review.banding_cards) == 1  # and the banding pair is invisible
    assert review.save_enabled is True
    for card in (*review.readability_cards, *review.banding_cards):
        assert card.actions == (CONTRAST_ACTION_ADJUST, CONTRAST_ACTION_SAVE_ANYWAY)


# --- The wire shape (meta.contrastWarnings) ---------------------------------------------


def test_wire_entries_carry_kind_preview_message_and_actions() -> None:
    colors = curated_colors() | {
        "rowText": "#c9ced4",
        "rowAlternateBackground": "#111111",
    }
    entries = guardrail_warning_entries(run_template_guardrail(colors))
    kinds = {entry["kind"] for entry in entries}
    assert kinds == {KIND_READABILITY, KIND_BANDING}
    for entry in entries:
        assert set(entry["message"]) == {"whatHappened", "why", "whatNext"}
        assert "never blocked" in entry["message"]["whatNext"].lower()
        assert entry["actions"] == [CONTRAST_ACTION_ADJUST, CONTRAST_ACTION_SAVE_ANYWAY]
        assert entry["preview"]["sampleText"]
    readability = next(e for e in entries if e["kind"] == KIND_READABILITY)
    assert readability["minimum"] == CONTRAST_MINIMUM
    assert readability["preview"]["textColor"] == "#c9ced4"
    banding = next(e for e in entries if e["kind"] == KIND_BANDING)
    assert banding["baseSlot"] == "rowBackground"
    assert banding["alternateSlot"] == "rowAlternateBackground"
    assert banding["subtletyCeiling"] == BANDING_SUBTLETY_CEILING
    assert banding["preview"]["alternateBackground"] == "#111111"
    assert banding["adjustStep"] == "rowColors"


def test_clean_review_answers_an_empty_list() -> None:
    assert guardrail_warning_entries(run_template_guardrail(curated_colors())) == []
