"""The template-management build (WTK-119): REQ-044/REQ-046 surfaces.

What the build guarantees over the WTK-113 flow and WTK-112 semantics:

- The launch set ships all four curated variants, every one a complete
  valid document that passes the contrast guardrail CLEAN — the curated
  set never warns (REQ-044/REQ-046).
- Wire conversion round-trips: a persisted record renders as a picker
  option, and a finished flow becomes exactly the write body the WTK-114
  surface accepts (the weld to TEMPLATE_CREATE).
- Never-hide mechanics: owner-only verbs on a system template answer an
  educate explanation, never a hidden or grayed action; deleting a user
  template confirms with honest soft-delete wording.
- The editor renders each slot-filling step's controls from the draft and
  commits edits only through the flow's always-valid mutators; the
  type-scale selector offers the fixed steps with px labels, never an
  arbitrary size (REQ-046).
- The row-theme step prefills from the RESOLVED theme, scoped to exactly
  what a row theme may touch (REQ-018/REQ-044).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from mentorapp.api import validate_template_write
from mentorapp.ui.template_flow import (
    ORIGIN_SYSTEM,
    ORIGIN_USER,
    ROW_THEME_CUSTOM,
    ROW_THEME_STANDARD,
    SLOT_FILLING_STEPS,
    STEP_COLOR_SLOTS,
    TemplateFlowError,
    TemplateOption,
    build_template_picker,
    finish_user_template,
    start_user_template,
)
from mentorapp.ui.template_manager import (
    ACTION_DELETE,
    ACTION_HELP,
    ACTION_RENAME,
    ACTION_SELECT,
    CONTROL_COLOR_SLOT,
    CONTROL_FONT_SLOT,
    CONTROL_ROW_HEIGHT,
    CONTROL_SIZE_STEP,
    FONT_WEIGHT_REGULAR,
    LAUNCH_TEMPLATE_NAMES,
    LAUNCH_TEMPLATES,
    TEMPLATE_ACTIONS,
    apply_control_edit,
    decide_template_action,
    editor_step_controls,
    launch_template_options,
    row_theme_controls,
    stored_template_option,
    template_create_payload,
    type_scale_labels,
)
from mentorapp.ui.theming import (
    COLOR_SLOTS,
    FONT_SLOTS,
    LAUNCH_TEMPLATE_KEYS,
    ROW_HEIGHT_STEPS,
    ROW_THEME_COLOR_SLOTS,
    STANDARD_TEMPLATE,
    TYPE_SCALE_STEPS,
    ThemeLayers,
    ThemingError,
    check_template_contrast,
    validate_template,
)

DARK = LAUNCH_TEMPLATES["dark"]


def dark_option() -> TemplateOption:
    return TemplateOption(template_key="dark", template_name="Dark", document=DARK)


# --- The curated launch set -----------------------------------------------------------


def test_launch_set_ships_all_four_variants() -> None:
    assert tuple(LAUNCH_TEMPLATES) == LAUNCH_TEMPLATE_KEYS
    assert LAUNCH_TEMPLATES["standard"] is STANDARD_TEMPLATE
    for document in LAUNCH_TEMPLATES.values():
        validate_template(document)


def test_launch_set_is_guardrail_clean() -> None:
    # Curated means curated: no launch template may ship a readability warning.
    for key, document in LAUNCH_TEMPLATES.items():
        assert check_template_contrast(document["colors"]) == (), key


def test_launch_variants_carry_their_purpose() -> None:
    assert LAUNCH_TEMPLATES["compact"]["rowHeight"] == "compact"
    assert LAUNCH_TEMPLATES["compact"]["sizeStep"] == "sm"
    assert LAUNCH_TEMPLATES["largePrint"]["rowHeight"] == "large"
    assert LAUNCH_TEMPLATES["largePrint"]["sizeStep"] == "xl"
    # Dark is a night palette: rows are dark, text is light.
    assert DARK["colors"]["rowBackground"].lower() != "#ffffff"
    assert int(DARK["colors"]["rowBackground"][1:3], 16) < 0x40
    assert int(DARK["colors"]["rowText"][1:3], 16) > 0xC0


def test_launch_variants_share_the_slot_structure() -> None:
    # Same fixed slots everywhere: template x panel combinations always work.
    for document in LAUNCH_TEMPLATES.values():
        assert sorted(document["colors"]) == sorted(COLOR_SLOTS)
        assert sorted(document["fonts"]) == sorted(FONT_SLOTS)


def test_launch_options_feed_the_picker_org_default_first() -> None:
    picker = build_template_picker(
        system_templates=launch_template_options(), user_templates=()
    )
    assert [entry.template_key for entry in picker.entries] == list(LAUNCH_TEMPLATE_KEYS)
    assert picker.entries[0].active
    assert [entry.template_name for entry in picker.entries] == [
        LAUNCH_TEMPLATE_NAMES[key] for key in LAUNCH_TEMPLATE_KEYS
    ]


# --- Wire conversion ------------------------------------------------------------------


def stored_record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "colorTemplateID": uuid.UUID("00000000-0000-7000-8000-000000000001"),
        "colorTemplateName": "Compact",
        "colorSlots": dict(STANDARD_TEMPLATE["colors"]),
        "fontSlots": {
            slot: {"stepKey": "sm", "fontFamily": "Inter", "fontWeight": 400}
            for slot in FONT_SLOTS
        },
        "typeStepChoice": "sm",
        "launchSetKey": "compact",
    }
    record.update(overrides)
    return record


def test_stored_template_option_renders_the_persisted_record() -> None:
    option = stored_template_option(stored_record())
    assert option.template_key == "00000000-0000-7000-8000-000000000001"
    assert option.document["sizeStep"] == "sm"
    assert option.document["fonts"] == {"uiFont": "Inter", "dataFont": "Inter"}
    # A launch row recovers its curated row height; user rows render standard.
    assert option.document["rowHeight"] == "compact"
    user_row = stored_template_option(stored_record(launchSetKey=None))
    assert user_row.document["rowHeight"] == "standard"


def test_stored_template_option_rejects_incomplete_slots() -> None:
    broken = stored_record()
    del broken["colorSlots"]["accent"]
    with pytest.raises(ThemingError):
        stored_template_option(broken)


def test_template_create_payload_passes_the_wtk114_write_gate() -> None:
    draft = start_user_template(dark_option())
    finished = finish_user_template(draft, name="My night theme")
    payload = template_create_payload(finished)
    validate_template_write(
        color_slots=payload["colorSlots"],
        font_slots=payload["fontSlots"],
        type_step_choice=payload["typeStepChoice"],
        scale_steps=tuple(TYPE_SCALE_STEPS),
    )
    assert payload["colorTemplateName"] == "My night theme"
    assert payload["templateType"] == "user"
    assert payload["typeStepChoice"] == DARK["sizeStep"]
    for slot in FONT_SLOTS:
        spec = payload["fontSlots"][slot]
        assert spec["fontFamily"] == DARK["fonts"][slot]
        assert spec["fontWeight"] == FONT_WEIGHT_REGULAR
        assert spec["stepKey"] == DARK["sizeStep"]


# --- Never-hide management actions ----------------------------------------------------


def test_every_action_is_always_listed_and_help_is_last() -> None:
    assert TEMPLATE_ACTIONS[-1] == ACTION_HELP
    assert len(set(TEMPLATE_ACTIONS)) == len(TEMPLATE_ACTIONS)


def test_owner_only_actions_on_a_system_template_educate() -> None:
    decision = decide_template_action(
        ACTION_RENAME, origin=ORIGIN_SYSTEM, template_name="Standard"
    )
    assert not decision.allowed
    assert decision.explanation is not None
    assert "Standard" in decision.explanation.what_happened
    assert decision.explanation.why
    assert "copy" in decision.explanation.what_next.lower()
    # Never a refusal for the verbs anyone may run.
    assert decide_template_action(
        ACTION_SELECT, origin=ORIGIN_SYSTEM, template_name="Standard"
    ).allowed


def test_deleting_a_user_template_confirms_with_honest_soft_delete_wording() -> None:
    decision = decide_template_action(
        ACTION_DELETE, origin=ORIGIN_USER, template_name="My night theme"
    )
    assert decision.allowed
    assert decision.confirmation is not None
    assert "My night theme" in decision.confirmation
    assert "administrator can restore" in decision.confirmation
    assert "cannot be undone" not in decision.confirmation.lower()


def test_unknown_actions_are_contract_errors() -> None:
    with pytest.raises(TemplateFlowError):
        decide_template_action("purge", origin=ORIGIN_USER, template_name="X")


# --- Slot-filling editor controls -----------------------------------------------------


def test_color_steps_render_one_control_per_owned_slot() -> None:
    draft = start_user_template(dark_option())
    for step, slots in STEP_COLOR_SLOTS.items():
        controls = editor_step_controls(draft, step)
        assert [control.slot for control in controls] == list(slots)
        assert all(control.control == CONTROL_COLOR_SLOT for control in controls)
        assert all(control.value == DARK["colors"][control.slot] for control in controls)


def test_fonts_and_scale_steps_render_their_controls() -> None:
    draft = start_user_template(dark_option())
    fonts = editor_step_controls(draft, "fonts")
    assert [control.slot for control in fonts] == list(FONT_SLOTS)
    size_step, row_height = editor_step_controls(draft, "scale")
    assert size_step.control == CONTROL_SIZE_STEP
    assert size_step.choices == tuple(TYPE_SCALE_STEPS)
    assert size_step.label == type_scale_labels()[DARK["sizeStep"]]
    assert row_height.control == CONTROL_ROW_HEIGHT
    assert row_height.choices == ROW_HEIGHT_STEPS


def test_type_scale_labels_show_px_sizes_for_the_fixed_steps_only() -> None:
    labels = type_scale_labels()
    assert sorted(labels) == sorted(TYPE_SCALE_STEPS)
    for step, size in TYPE_SCALE_STEPS.items():
        assert labels[step] == f"{step} — {size} px"


def test_non_slot_steps_refuse_control_rendering() -> None:
    draft = start_user_template(dark_option())
    for step in ("basis", "review"):
        assert step in SLOT_FILLING_STEPS
        with pytest.raises(TemplateFlowError):
            editor_step_controls(draft, step)


def test_apply_control_edit_commits_through_the_flow_mutators() -> None:
    draft = start_user_template(dark_option())
    (accent,) = (
        control
        for control in editor_step_controls(draft, "chromeColors")
        if control.slot == "accent"
    )
    apply_control_edit(draft, accent, "#ff8800")
    assert draft.document["colors"]["accent"] == "#ff8800"
    size_step, row_height = editor_step_controls(draft, "scale")
    apply_control_edit(draft, size_step, "lg")
    apply_control_edit(draft, row_height, "large")
    assert draft.document["sizeStep"] == "lg"
    assert draft.document["rowHeight"] == "large"
    # A bad value rejects loudly and the draft is untouched (no half-applying).
    with pytest.raises(ThemingError):
        apply_control_edit(draft, accent, "orange")
    assert draft.document["colors"]["accent"] == "#ff8800"


# --- Per-grid row-theme override controls ---------------------------------------------


def test_row_theme_controls_prefill_from_the_resolved_theme() -> None:
    layers = ThemeLayers(org_default=STANDARD_TEMPLATE, user_choice=DARK)
    step = row_theme_controls(layers)
    assert step.choice == ROW_THEME_STANDARD
    assert step.marks_view_modified
    assert step.scope_note.what_next
    color_controls = [c for c in step.controls if c.control == CONTROL_COLOR_SLOT]
    assert [control.slot for control in color_controls] == list(ROW_THEME_COLOR_SLOTS)
    # Prefilled from what actually shows through — the user's DARK choice.
    assert all(c.value == DARK["colors"][c.slot] for c in color_controls)
    assert {c.control for c in step.controls} == {
        CONTROL_COLOR_SLOT,
        CONTROL_ROW_HEIGHT,
        CONTROL_FONT_SLOT,
        CONTROL_SIZE_STEP,
    }


def test_row_theme_controls_reflect_an_active_override() -> None:
    override = {"colors": {"rowBackground": "#101418"}, "rowHeight": "compact"}
    layers = ThemeLayers(org_default=STANDARD_TEMPLATE, user_choice=DARK, row_theme=override)
    step = row_theme_controls(layers)
    assert step.choice == ROW_THEME_CUSTOM
    (row_background,) = (c for c in step.controls if c.slot == "rowBackground")
    assert row_background.value == "#101418"
    (row_height,) = (c for c in step.controls if c.control == CONTROL_ROW_HEIGHT)
    assert row_height.value == "compact"
