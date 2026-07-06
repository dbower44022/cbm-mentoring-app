"""Tests for the universal row-banding design (WTK-207, REQ-092)."""

from __future__ import annotations

from typing import Any

import pytest

from mentorapp.ui.row_banding import (
    BAND_ALTERNATE,
    BAND_BASE,
    BANDING_BASE_SLOT,
    BANDING_DISTINCTION_FLOOR,
    BANDING_HAS_NO_EXEMPTIONS,
    BANDING_SLOT,
    BANDING_SUBTLETY_CEILING,
    CHROME_LIST_SURFACES,
    VIEW_BACKED_SURFACES,
    BandedSurface,
    assign_bands,
    band_for_position,
    check_banding_subtlety,
    effective_row_background,
    resolve_banding,
)
from mentorapp.ui.theming import (
    COLOR_SLOTS,
    LAYER_ORG_DEFAULT,
    LAYER_ROW_THEME,
    LAYER_USER_CHOICE,
    ROW_THEME_COLOR_SLOTS,
    STANDARD_TEMPLATE,
    ThemeLayers,
    ThemingError,
    validate_template,
)


def _template(**color_overrides: str) -> dict[str, Any]:
    colors = dict(STANDARD_TEMPLATE["colors"])
    colors.update(color_overrides)
    return {
        "colors": colors,
        "fonts": dict(STANDARD_TEMPLATE["fonts"]),
        "rowHeight": STANDARD_TEMPLATE["rowHeight"],
        "sizeStep": STANDARD_TEMPLATE["sizeStep"],
    }


# --- The slot IS the mechanism -------------------------------------------------------


def test_banding_pair_lives_in_the_fixed_slot_structure() -> None:
    # Template completeness (REQ-044) is what guarantees REQ-092's "the
    # banding color is a slot every launch template fills".
    assert BANDING_BASE_SLOT in COLOR_SLOTS
    assert BANDING_SLOT in COLOR_SLOTS
    validate_template(STANDARD_TEMPLATE)


def test_a_view_row_theme_may_restyle_the_banding_pair() -> None:
    assert BANDING_SLOT in ROW_THEME_COLOR_SLOTS


def test_a_template_cannot_omit_the_banding_slot() -> None:
    document = _template()
    del document["colors"][BANDING_SLOT]
    with pytest.raises(ThemingError, match=BANDING_SLOT):
        validate_template(document)


# --- No list is exempt ---------------------------------------------------------------


def test_every_surface_kind_bands() -> None:
    assert BANDING_HAS_NO_EXEMPTIONS
    assert set(VIEW_BACKED_SURFACES) | set(CHROME_LIST_SURFACES) == set(BandedSurface)
    for surface in BandedSurface:
        pair = resolve_banding(surface, ThemeLayers(org_default=STANDARD_TEMPLATE))
        assert pair.base == STANDARD_TEMPLATE["colors"][BANDING_BASE_SLOT]
        assert pair.alternate == STANDARD_TEMPLATE["colors"][BANDING_SLOT]
        assert pair.provenance[BANDING_SLOT] == LAYER_ORG_DEFAULT


# --- Where the pair resolves from ----------------------------------------------------


def test_chrome_lists_band_from_the_app_wide_template_choice() -> None:
    choice = _template(**{BANDING_BASE_SLOT: "#20242a", BANDING_SLOT: "#262b33"})
    for surface in CHROME_LIST_SURFACES:
        pair = resolve_banding(
            surface, ThemeLayers(org_default=STANDARD_TEMPLATE, user_choice=choice)
        )
        assert (pair.base, pair.alternate) == ("#20242a", "#262b33")
        assert pair.provenance[BANDING_SLOT] == LAYER_USER_CHOICE


def test_chrome_lists_reject_a_row_theme_loudly() -> None:
    layers = ThemeLayers(
        org_default=STANDARD_TEMPLATE, row_theme={"colors": {BANDING_SLOT: "#e0e0e0"}}
    )
    with pytest.raises(ThemingError, match="no view"):
        resolve_banding(BandedSurface.MESSAGE_LIST, layers)


def test_view_backed_surfaces_honor_the_row_theme_layer() -> None:
    layers = ThemeLayers(
        org_default=STANDARD_TEMPLATE, row_theme={"colors": {BANDING_SLOT: "#e8f0e8"}}
    )
    for surface in VIEW_BACKED_SURFACES:
        pair = resolve_banding(surface, layers)
        assert pair.alternate == "#e8f0e8"
        assert pair.provenance[BANDING_SLOT] == LAYER_ROW_THEME
        # The base slot came through untouched from layer one.
        assert pair.provenance[BANDING_BASE_SLOT] == LAYER_ORG_DEFAULT


# --- Alternation semantics -----------------------------------------------------------


def test_first_row_is_always_the_base_color() -> None:
    assert band_for_position(1) == BAND_BASE
    assert band_for_position(2) == BAND_ALTERNATE
    assert [band_for_position(n) for n in range(1, 6)] == [
        BAND_BASE,
        BAND_ALTERNATE,
        BAND_BASE,
        BAND_ALTERNATE,
        BAND_BASE,
    ]


def test_positions_are_one_based() -> None:
    with pytest.raises(ThemingError, match="1-based"):
        band_for_position(0)


def test_structural_rows_never_consume_a_banding_position() -> None:
    # A grouped grid: header, two data rows, header, data row, footer. The
    # data-row cadence runs base/alternate/base straight through.
    bands = assign_bands([False, True, True, False, True, False])
    assert bands == (None, BAND_BASE, BAND_ALTERNATE, None, BAND_BASE, None)


def test_state_backgrounds_always_beat_banding() -> None:
    pair = resolve_banding(BandedSurface.GRID, ThemeLayers(org_default=STANDARD_TEMPLATE))
    assert effective_row_background(pair, 2) == pair.alternate
    selected = STANDARD_TEMPLATE["colors"]["selectedRowBackground"]
    assert effective_row_background(pair, 2, state_background=selected) == selected


# --- Subtle, never pronounced: warn, never block -------------------------------------


def test_launch_default_pair_raises_no_warning() -> None:
    assert check_banding_subtlety(STANDARD_TEMPLATE["colors"]) == ()


def test_pronounced_banding_warns_in_educate_voice() -> None:
    colors = _template(**{BANDING_BASE_SLOT: "#ffffff", BANDING_SLOT: "#b0b8c0"})["colors"]
    warnings = check_banding_subtlety(colors)
    assert len(warnings) == 1
    warning = warnings[0]
    assert warning.ratio > BANDING_SUBTLETY_CEILING
    assert "pronounced" in warning.message.what_happened
    assert "never blocked" in warning.message.what_next


def test_invisible_banding_warns_in_educate_voice() -> None:
    colors = _template(**{BANDING_BASE_SLOT: "#ffffff", BANDING_SLOT: "#ffffff"})["colors"]
    warnings = check_banding_subtlety(colors)
    assert len(warnings) == 1
    warning = warnings[0]
    assert warning.ratio <= BANDING_DISTINCTION_FLOOR
    assert "disappears" in warning.message.why
    assert "never blocked" in warning.message.what_next
