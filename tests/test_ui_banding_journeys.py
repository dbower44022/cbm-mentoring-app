"""Prototype-gate delta journeys: universal row banding (REQ-092, WTK-209).

Chained journeys over the WTK-207/208 delta — not per-fact units
(``test_ui_row_banding`` owns the design's units, ``test_ui_contrast_guardrail``
the warning cards): each scenario drives the launch set, the template
management flow, and the banding resolution together, so the REQ-092
acceptance summary is proven end to end:

- Every launch template (Standard, Compact, Large print, Dark) defines the
  banding slot, and under each one EVERY row-oriented surface renders a
  strict two-color alternation — visibly distinct backgrounds, structural
  rows never shifting the cadence.
- The contrast between bands is subtle on the whole curated set: inside the
  design's subtlety bounds and clean through the save-time guardrail.
- Changing the banding slot in a color template changes banding app-wide
  for users of that template: one slot edit in the management flow travels
  through save, the persisted-record round-trip, and the picker selection
  to every surface at once — including the real grid row-render path —
  while a user still on the org default is untouched.
"""

from __future__ import annotations

from typing import Any

from mentorapp.ui.conditional_formatting import (
    DECIDED_BY_BANDING,
    DECIDED_BY_SELECTION,
    resolve_grid_render,
    resolve_row_render,
)
from mentorapp.ui.contrast_guardrail import run_template_guardrail
from mentorapp.ui.row_banding import (
    BANDING_BASE_SLOT,
    BANDING_DISTINCTION_FLOOR,
    BANDING_SLOT,
    BANDING_SUBTLETY_CEILING,
    BandedSurface,
    assign_bands,
    effective_row_background,
    resolve_banding,
)
from mentorapp.ui.template_flow import (
    TemplateOption,
    build_template_picker,
    finish_user_template,
    select_template,
    start_user_template,
)
from mentorapp.ui.template_manager import (
    CONTROL_COLOR_SLOT,
    LAUNCH_TEMPLATE_NAMES,
    LAUNCH_TEMPLATES,
    apply_control_edit,
    editor_step_controls,
    launch_template_options,
    stored_template_option,
    template_create_payload,
)
from mentorapp.ui.theming import (
    LAUNCH_TEMPLATE_KEYS,
    LAYER_ORG_DEFAULT,
    LAYER_USER_CHOICE,
    STANDARD_TEMPLATE,
    ThemeLayers,
    contrast_ratio,
    validate_template,
)

# A realistic grouped list: header, three data rows, another header, two
# data rows — the shape Home's dashlets and a grouped grid both render.
GROUPED_ROWS = (False, True, True, True, False, True, True)

# The personal copy's new alternate: near Dark's row background, so the
# journey's edit stays inside REQ-092's "subtle" and the guardrail stays
# clean — the test proves propagation, not a guardrail trip.
NEW_ALTERNATE = "#232c3b"


def _layers_for(document: dict[str, Any]) -> ThemeLayers:
    return ThemeLayers(org_default=STANDARD_TEMPLATE, user_choice=document)


# --- Every launch template defines the slot; every surface alternates ----------------


def test_every_launch_template_bands_every_surface() -> None:
    # The acceptance summary names the set exactly.
    assert tuple(LAUNCH_TEMPLATES) == LAUNCH_TEMPLATE_KEYS
    assert tuple(LAUNCH_TEMPLATE_NAMES[key] for key in LAUNCH_TEMPLATE_KEYS) == (
        "Standard",
        "Compact",
        "Large print",
        "Dark",
    )
    for document in LAUNCH_TEMPLATES.values():
        validate_template(document)
        colors: dict[str, str] = document["colors"]
        # Distinct backgrounds are what make the alternation visible at all.
        assert colors[BANDING_BASE_SLOT] != colors[BANDING_SLOT]
        for surface in BandedSurface:
            pair = resolve_banding(surface, _layers_for(document))
            assert (pair.base, pair.alternate) == (
                colors[BANDING_BASE_SLOT],
                colors[BANDING_SLOT],
            )
            # Render the grouped list: data rows alternate the exact pair,
            # structural rows never consume a position.
            bands = assign_bands(GROUPED_ROWS)
            assert [band is None for band in bands] == [
                not is_data for is_data in GROUPED_ROWS
            ]
            backgrounds = [
                effective_row_background(pair, position) for position in range(1, 6)
            ]
            assert backgrounds == [
                pair.base,
                pair.alternate,
                pair.base,
                pair.alternate,
                pair.base,
            ]


def test_launch_set_banding_is_subtle_never_pronounced() -> None:
    for document in LAUNCH_TEMPLATES.values():
        colors: dict[str, str] = document["colors"]
        ratio = contrast_ratio(colors[BANDING_BASE_SLOT], colors[BANDING_SLOT])
        # Distinguishable but subtle — strictly inside the design's bounds.
        assert BANDING_DISTINCTION_FLOOR < ratio <= BANDING_SUBTLETY_CEILING
        assert run_template_guardrail(colors).banding_cards == ()


# --- One slot edit changes banding app-wide for users of that template ---------------


def _customized_dark_option() -> TemplateOption:
    """Drive the real management flow: copy Dark, edit ONLY the banding slot,
    pass the guardrail, save, and round-trip the persisted record."""
    dark = next(
        option for option in launch_template_options() if option.template_key == "dark"
    )
    draft = start_user_template(dark)
    control = next(
        control
        for control in editor_step_controls(draft, "rowColors")
        if control.slot == BANDING_SLOT
    )
    assert control.control == CONTROL_COLOR_SLOT
    assert control.value == dark.document["colors"][BANDING_SLOT]
    apply_control_edit(draft, control, NEW_ALTERNATE)
    # A subtle personal pair sails through the save-time guardrail.
    assert run_template_guardrail(draft.document["colors"]).banding_cards == ()
    finished = finish_user_template(draft, name="My night theme")
    record = {
        **template_create_payload(finished),
        "colorTemplateID": "00000000-0000-7000-8000-0000000000f2",
        "launchSetKey": None,
    }
    option = stored_template_option(record)
    # The edited slot survives the write/read seam byte for byte.
    assert option.document["colors"][BANDING_SLOT] == NEW_ALTERNATE
    return option


def test_changing_the_banding_slot_changes_banding_app_wide() -> None:
    option = _customized_dark_option()
    picker = build_template_picker(
        system_templates=list(launch_template_options()), user_templates=[option]
    )
    selection = select_template(picker, option.template_key)
    assert selection.as_preference_value() == {"templateKey": option.template_key}
    chosen = _layers_for(option.document)
    for surface in BandedSurface:
        pair = resolve_banding(surface, chosen)
        assert pair.alternate == NEW_ALTERNATE
        assert pair.base == option.document["colors"][BANDING_BASE_SLOT]
        assert pair.provenance[BANDING_SLOT] == LAYER_USER_CHOICE
    # A peer still on the org default is untouched — the change is app-wide
    # for USERS OF THAT TEMPLATE, never for everyone.
    for surface in BandedSurface:
        pair = resolve_banding(surface, ThemeLayers(org_default=STANDARD_TEMPLATE))
        assert pair.alternate == STANDARD_TEMPLATE["colors"][BANDING_SLOT]
        assert pair.provenance[BANDING_SLOT] == LAYER_ORG_DEFAULT


def test_changed_template_reaches_the_real_grid_render() -> None:
    option = _customized_dark_option()
    plan = resolve_grid_render(_layers_for(option.document))
    first = resolve_row_render(plan, {}, 1)
    second = resolve_row_render(plan, {}, 2)
    assert first.background == option.document["colors"][BANDING_BASE_SLOT]
    assert second.background == NEW_ALTERNATE
    assert first.provenance["rowBackground"] == DECIDED_BY_BANDING
    assert second.provenance["rowBackground"] == DECIDED_BY_BANDING
    # Banding stays the floor: selection outranks it even on an even row.
    selected = resolve_row_render(plan, {}, 2, selected=True)
    assert selected.background == option.document["colors"]["selectedRowBackground"]
    assert selected.provenance["rowBackground"] == DECIDED_BY_SELECTION
