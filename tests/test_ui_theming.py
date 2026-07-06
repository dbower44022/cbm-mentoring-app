"""Tests for the theming resolution semantics (WTK-112, REQ-044/REQ-046)."""

from __future__ import annotations

from typing import Any

import pytest

from mentorapp.ui.theming import (
    CHROME_COLOR_SLOTS,
    COLOR_SLOTS,
    CONTRAST_CHECKED_PAIRS,
    CONTRAST_MINIMUM,
    GUARDRAIL_NEVER_BLOCKS,
    LAYER_ORG_DEFAULT,
    LAYER_ROW_THEME,
    LAYER_USER_CHOICE,
    LAUNCH_TEMPLATE_KEYS,
    ORG_DEFAULT_TEMPLATE_KEY,
    ROW_THEME_COLOR_SLOTS,
    STANDARD_TEMPLATE,
    TYPE_SCALE_STEPS,
    ContrastWarning,
    EffectiveGridTheme,
    ThemeLayers,
    ThemingError,
    check_template_contrast,
    contrast_ratio,
    relative_luminance,
    resolve_effective_grid_theme,
    review_user_template,
    validate_row_theme,
    validate_template,
)


def _template(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "colors": dict(STANDARD_TEMPLATE["colors"]),
        "fonts": dict(STANDARD_TEMPLATE["fonts"]),
        "rowHeight": STANDARD_TEMPLATE["rowHeight"],
        "sizeStep": STANDARD_TEMPLATE["sizeStep"],
    }
    document.update(overrides)
    return document


def _dark_choice() -> dict[str, Any]:
    colors = {slot: "#20242a" for slot in COLOR_SLOTS}
    colors |= {
        "rowText": "#e8eaed",
        "selectedRowText": "#ffffff",
        "headerText": "#e8eaed",
        "groupHeaderText": "#e8eaed",
        "rowAlternateBackground": "#262b33",
        "selectedRowBackground": "#0b3d61",
        "headerBackground": "#11151a",
        "groupHeaderBackground": "#11151a",
    }
    return _template(colors=colors, fonts={"uiFont": "Inter", "dataFont": "Roboto Mono"})


# --- Slot structure: fixed, never freeform (REQ-044) --------------------------------


def test_slot_vocabulary_is_fixed_and_row_scope_is_a_subset() -> None:
    assert set(ROW_THEME_COLOR_SLOTS) < set(COLOR_SLOTS)
    assert not set(CHROME_COLOR_SLOTS) & set(ROW_THEME_COLOR_SLOTS)
    assert ORG_DEFAULT_TEMPLATE_KEY in LAUNCH_TEMPLATE_KEYS
    validate_template(STANDARD_TEMPLATE)


def test_template_rejects_freeform_and_partial_documents() -> None:
    with pytest.raises(ThemingError, match="unknown template keys"):
        validate_template(_template(customCss=".row { color: red }"))
    colors = dict(STANDARD_TEMPLATE["colors"])
    colors["myFavoriteButton"] = "#ff0000"
    with pytest.raises(ThemingError, match="every fixed color slot"):
        validate_template(_template(colors=colors))
    incomplete = dict(STANDARD_TEMPLATE["colors"])
    del incomplete["accent"]
    with pytest.raises(ThemingError, match="missing \\['accent'\\]"):
        validate_template(_template(colors=incomplete))
    bad_value = dict(STANDARD_TEMPLATE["colors"], accent="dodgerblue")
    with pytest.raises(ThemingError, match="#rrggbb"):
        validate_template(_template(colors=bad_value))


def test_template_sizes_come_from_the_shared_scale_never_arbitrary() -> None:
    with pytest.raises(ThemingError, match="type-scale step"):
        validate_template(_template(sizeStep="13px"))
    with pytest.raises(ThemingError, match="rowHeight"):
        validate_template(_template(rowHeight=27))
    with pytest.raises(ThemingError, match="font slots"):
        validate_template(_template(fonts={"uiFont": "Inter"}))


def test_row_theme_rejects_chrome_slots_and_arbitrary_sizes() -> None:
    validate_row_theme({})
    validate_row_theme({"rowHeight": "compact"})
    validate_row_theme({"colors": {"rowBackground": "#fffbe6"}})
    validate_row_theme({"font": {"fontSlot": "dataFont", "sizeStep": "lg"}})
    with pytest.raises(ThemingError, match="its grid's rows only"):
        validate_row_theme({"colors": {"headerBackground": "#000000"}})
    with pytest.raises(ThemingError, match="its grid's rows only"):
        validate_row_theme({"colors": {"statusNegative": "#ff0000"}})
    with pytest.raises(ThemingError, match="unknown row-theme keys"):
        validate_row_theme({"chrome": {"accent": "#ff0000"}})
    with pytest.raises(ThemingError, match="never a family or size"):
        validate_row_theme({"font": {"family": "Comic Sans", "size": 18}})
    with pytest.raises(ThemingError, match="type-scale step"):
        validate_row_theme({"font": {"sizeStep": "18px"}})


# --- Three-layer precedence (REQ-044) ------------------------------------------------


def test_org_default_alone_decides_everything() -> None:
    resolved = resolve_effective_grid_theme(ThemeLayers(org_default=STANDARD_TEMPLATE))
    assert isinstance(resolved, EffectiveGridTheme)
    assert resolved.colors == STANDARD_TEMPLATE["colors"]
    assert resolved.row_height == "standard"
    assert resolved.size_step == "md"
    assert set(resolved.provenance.values()) == {LAYER_ORG_DEFAULT}


def test_user_choice_replaces_the_default_wholesale_app_wide() -> None:
    choice = _dark_choice()
    resolved = resolve_effective_grid_theme(
        ThemeLayers(org_default=STANDARD_TEMPLATE, user_choice=choice)
    )
    assert resolved.colors == choice["colors"]
    assert resolved.fonts == choice["fonts"]
    # Wholesale: no slot survives from the org default once a choice is set.
    assert set(resolved.provenance.values()) == {LAYER_USER_CHOICE}


def test_row_theme_overlays_only_what_it_names_for_this_grid() -> None:
    row_theme = {
        "rowHeight": "compact",
        "colors": {"rowBackground": "#fffbe6", "rowText": "#332200"},
        "font": {"sizeStep": "sm"},
    }
    resolved = resolve_effective_grid_theme(
        ThemeLayers(
            org_default=STANDARD_TEMPLATE, user_choice=_dark_choice(), row_theme=row_theme
        )
    )
    assert resolved.colors["rowBackground"] == "#fffbe6"
    assert resolved.row_height == "compact"
    assert resolved.size_step == "sm"
    assert resolved.provenance["rowBackground"] == LAYER_ROW_THEME
    assert resolved.provenance["rowHeight"] == LAYER_ROW_THEME
    assert resolved.provenance["sizeStep"] == LAYER_ROW_THEME
    # Unnamed settings show through from the chosen template, chrome included.
    assert resolved.colors["headerBackground"] == _dark_choice()["colors"]["headerBackground"]
    assert resolved.provenance["headerBackground"] == LAYER_USER_CHOICE
    assert resolved.provenance["selectedRowText"] == LAYER_USER_CHOICE
    assert resolved.provenance["fontSlot"] == LAYER_USER_CHOICE


def test_null_row_theme_means_the_standard_theme() -> None:
    with_none = resolve_effective_grid_theme(
        ThemeLayers(org_default=STANDARD_TEMPLATE, row_theme=None)
    )
    without = resolve_effective_grid_theme(ThemeLayers(org_default=STANDARD_TEMPLATE))
    assert with_none == without


def test_resolution_validates_layers_before_applying_any() -> None:
    with pytest.raises(ThemingError, match="its grid's rows only"):
        resolve_effective_grid_theme(
            ThemeLayers(
                org_default=STANDARD_TEMPLATE,
                row_theme={"colors": {"appBackground": "#000000"}},
            )
        )


# --- Contrast guardrail: warn with preview, never block (REQ-046) -------------------


def test_contrast_math_matches_wcag_anchors() -> None:
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0)
    assert relative_luminance("#ffffff") == pytest.approx(1.0)
    with pytest.raises(ThemingError, match="#rrggbb"):
        relative_luminance("white")


def test_curated_standard_template_passes_the_guardrail_clean() -> None:
    assert review_user_template(STANDARD_TEMPLATE) == ()


def test_unreadable_pair_warns_with_preview_and_educate_message() -> None:
    colors = dict(STANDARD_TEMPLATE["colors"], rowText="#c9ced4")
    warnings = check_template_contrast(colors)
    pairs = {(w.text_slot, w.background_slot) for w in warnings}
    assert ("rowText", "rowBackground") in pairs
    warning = next(w for w in warnings if w.background_slot == "rowBackground")
    assert isinstance(warning, ContrastWarning)
    assert warning.ratio < CONTRAST_MINIMUM
    assert warning.preview.text_color == "#c9ced4"
    assert warning.preview.background_color == colors["rowBackground"]
    assert warning.preview.sample_text
    assert "never blocked" in warning.message.what_next.lower()
    assert set(pairs) <= set(CONTRAST_CHECKED_PAIRS)


def test_guardrail_warns_but_never_blocks_the_save() -> None:
    assert GUARDRAIL_NEVER_BLOCKS
    grey_on_grey = dict(STANDARD_TEMPLATE["colors"])
    grey_on_grey |= {slot: "#9aa2aa" for slot in ROW_THEME_COLOR_SLOTS}
    grey_on_grey |= {"headerText": "#8a9199", "headerBackground": "#9aa2aa"}
    # Every checked pair is unreadable, and review still RETURNS (no raise).
    warnings = review_user_template(_template(colors=grey_on_grey))
    assert len(warnings) == len(CONTRAST_CHECKED_PAIRS)


def test_guardrail_still_rejects_structure_violations() -> None:
    colors = dict(STANDARD_TEMPLATE["colors"])
    colors["perElementHack"] = "#123456"
    with pytest.raises(ThemingError):
        review_user_template(_template(colors=colors))


def test_type_scale_steps_are_ordered_and_named() -> None:
    sizes = [TYPE_SCALE_STEPS[step] for step in ("xs", "sm", "md", "lg", "xl")]
    assert sizes == sorted(sizes)
