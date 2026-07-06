"""Tests for the look-and-feel data model (WTK-111, PI-007), reconciled to the
UI design (WSK-019, FND-905/906/907): the persisted vocabulary IS the UI's
fixed 15-slot + 2-font structure, formatting effects name status slots, and
the three-layer model has no override table and no numeric precedence."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import mentorapp.storage as storage
from mentorapp.storage import (
    CHROME_COLOR_SLOTS,
    COLOR_SLOTS,
    CONDITION_OPERATORS,
    CONTRAST_GUARDRAIL_BEHAVIORS,
    FONT_SLOTS,
    FORMATTING_EFFECTS,
    LAUNCH_SETS,
    ROW_THEME_COLOR_SLOTS,
    SHARED_TYPE_SCALE_NAME,
    STATUS_COLOR_SLOTS,
    TEMPLATE_TYPES,
    TYPE_SCALE_DEFAULT_SIZES,
    TYPE_SCALE_STEPS,
    AppUser,
    ColorTemplate,
    ConditionalFormattingRule,
    GridView,
    TypeScale,
    shared_type_scale,
    utcnow,
)


def _user(session: Session, username: str = "mentor.one") -> AppUser:
    user = AppUser(crm_user_id=f"crm-{username}", username=username)
    session.add(user)
    session.flush()
    return user


def _scale(session: Session, name: str = "App scale") -> TypeScale:
    scale = TypeScale(
        type_scale_name=name,
        scale_steps={"xs": 11, "sm": 12, "md": 14, "lg": 16, "xl": 20},
    )
    session.add(scale)
    session.flush()
    return scale


def _template(
    session: Session,
    scale: TypeScale,
    name: str = "Standard",
    *,
    owner: uuid.UUID | None = None,
    template_type: str = "system",
    launch_set_key: str | None = None,
) -> ColorTemplate:
    template = ColorTemplate(
        color_template_name=name,
        template_type=template_type,
        user_id=owner,
        type_scale_id=scale.type_scale_id,
        color_slots={slot: "#ffffff" for slot in COLOR_SLOTS},
        font_slots={slot: {"stepKey": "md"} for slot in FONT_SLOTS},
        launch_set_key=launch_set_key,
    )
    session.add(template)
    session.flush()
    return template


def _rule(
    session: Session,
    template: ColorTemplate,
    order: int,
    *,
    effect: str = "rowBackground",
) -> ConditionalFormattingRule:
    rule = ConditionalFormattingRule(
        color_template_id=template.color_template_id,
        condition_field="status",
        condition_operator="equals",
        condition_value={"value": "overdue"},
        effect=effect,
        # REQ-045: the applied color is a status slot, never a literal (FND-906).
        effect_slot="statusNegative",
        evaluation_order=order,
    )
    session.add(rule)
    session.flush()
    return rule


def test_vocabularies_match_the_standard() -> None:
    assert TEMPLATE_TYPES == ("system", "user")
    assert LAUNCH_SETS == ("standard", "compact", "largePrint", "dark")
    # FND-905/REQ-044: the persisted vocabulary is the UI design's fixed
    # 15-slot structure — chrome + row-scoped + status, the UI's exact names.
    assert CHROME_COLOR_SLOTS == (
        "appBackground",
        "panelBackground",
        "headerBackground",
        "headerText",
        "accent",
    )
    assert ROW_THEME_COLOR_SLOTS == (
        "rowBackground",
        "rowAlternateBackground",
        "rowText",
        "selectedRowBackground",
        "selectedRowText",
        "groupHeaderBackground",
        "groupHeaderText",
    )
    assert STATUS_COLOR_SLOTS == ("statusPositive", "statusWarning", "statusNegative")
    assert COLOR_SLOTS == CHROME_COLOR_SLOTS + ROW_THEME_COLOR_SLOTS + STATUS_COLOR_SLOTS
    assert len(COLOR_SLOTS) == 15
    assert FONT_SLOTS == ("uiFont", "dataFont")
    assert TYPE_SCALE_STEPS == ("xs", "sm", "md", "lg", "xl")
    # PI-007: the guardrail educates — warns, never blocks.
    assert CONTRAST_GUARDRAIL_BEHAVIORS == ("warn",)
    # The effect enum is limited to fixed slots (WTK-111).
    assert set(FORMATTING_EFFECTS) <= set(COLOR_SLOTS)
    assert "equals" in CONDITION_OPERATORS


def test_ui_and_storage_share_one_canonical_vocabulary() -> None:
    # FND-905: one canonical home — ui.theming re-exports THESE objects, so
    # the resolver's vocabulary can never drift from the persisted one.
    from mentorapp.ui import theming as ui_theming

    assert ui_theming.COLOR_SLOTS is COLOR_SLOTS
    assert ui_theming.FONT_SLOTS is FONT_SLOTS
    assert ui_theming.CHROME_COLOR_SLOTS is CHROME_COLOR_SLOTS
    assert ui_theming.ROW_THEME_COLOR_SLOTS is ROW_THEME_COLOR_SLOTS
    assert ui_theming.STATUS_COLOR_SLOTS is STATUS_COLOR_SLOTS
    # WTK-116: the design-default step sizes too — what the UI resolves as
    # the design default is exactly what migration 0011 persisted.
    assert ui_theming.TYPE_SCALE_STEPS is TYPE_SCALE_DEFAULT_SIZES


def test_default_sizes_cover_exactly_the_defined_steps() -> None:
    # REQ-046: the persisted default maps every defined step, no extras, and
    # sizes strictly ascend xs → xl (the shape the WTK-114 PATCH enforces).
    assert tuple(TYPE_SCALE_DEFAULT_SIZES) == TYPE_SCALE_STEPS
    sizes = list(TYPE_SCALE_DEFAULT_SIZES.values())
    assert sizes == sorted(set(sizes))


def test_new_scale_defaults_to_the_design_default_sizes(session: Session) -> None:
    scale = TypeScale(type_scale_name="Fresh scale")
    session.add(scale)
    session.flush()

    assert scale.scale_steps == TYPE_SCALE_DEFAULT_SIZES


def test_scale_rejects_off_scale_and_missing_steps() -> None:
    # WTK-116: the persistence boundary refuses to mint or drop a step.
    with pytest.raises(ValueError, match="off-scale \\['xxl'\\]"):
        TypeScale(
            type_scale_name="Minted",
            scale_steps={**TYPE_SCALE_DEFAULT_SIZES, "xxl": 28},
        )
    with pytest.raises(ValueError, match="missing"):
        TypeScale(type_scale_name="Partial", scale_steps={"xs": 11})


def test_template_rejects_off_scale_step_choice(session: Session) -> None:
    scale = _scale(session)

    with pytest.raises(ValueError, match="off-scale"):
        ColorTemplate(
            color_template_name="Huge",
            template_type="system",
            type_scale_id=scale.type_scale_id,
            color_slots={slot: "#ffffff" for slot in COLOR_SLOTS},
            font_slots={slot: {"stepKey": "md"} for slot in FONT_SLOTS},
            type_step_choice="huge",
        )


def test_template_rejects_off_scale_font_step(session: Session) -> None:
    scale = _scale(session)

    with pytest.raises(ValueError, match="fontSlots.uiFont"):
        ColorTemplate(
            color_template_name="Off scale",
            template_type="system",
            type_scale_id=scale.type_scale_id,
            color_slots={slot: "#ffffff" for slot in COLOR_SLOTS},
            font_slots={"uiFont": {"stepKey": "17px"}, "dataFont": {"stepKey": "md"}},
        )


def test_shared_type_scale_reads_the_one_live_seeded_row(session: Session) -> None:
    # Absent seed = broken deployment, answered loudly (never a silent None).
    with pytest.raises(LookupError, match="not seeded"):
        shared_type_scale(session)

    seeded = _scale(session, name=SHARED_TYPE_SCALE_NAME)
    assert shared_type_scale(session) is seeded

    # DB-S3: the default read excludes a soft-deleted shared row.
    seeded.deleted_at = utcnow()
    session.flush()
    with pytest.raises(LookupError):
        shared_type_scale(session)


def test_template_defaults_speak_the_ruled_contract(session: Session) -> None:
    template = _template(session, _scale(session))

    assert template.type_step_choice == "md"
    assert template.contrast_guardrail_behavior == "warn"
    assert template.launch_set_key is None
    assert template.row_version == 1


def test_type_scale_name_unique_among_live_rows(session: Session) -> None:
    first = _scale(session)

    with pytest.raises(IntegrityError):
        _scale(session)
    session.rollback()

    session.add(first)
    first.deleted_at = utcnow()
    session.flush()
    replacement = _scale(session)
    assert replacement.type_scale_id != first.type_scale_id


def test_system_template_names_unique_but_soft_deleted_names_reusable(
    session: Session,
) -> None:
    scale = _scale(session)
    first = _template(session, scale, launch_set_key="standard")

    with pytest.raises(IntegrityError):
        _template(session, scale)
    session.rollback()

    session.add_all([scale, first])
    first.deleted_at = utcnow()
    session.flush()
    assert _template(session, scale).color_template_name == "Standard"


def test_user_templates_may_shadow_system_names_but_not_their_own(
    session: Session,
) -> None:
    scale = _scale(session)
    _template(session, scale)
    owner = _user(session)
    other = _user(session, "mentor.two")

    # A user's copy may carry the system template's name...
    _template(session, scale, owner=owner.user_id, template_type="user")
    # ...and two owners never collide with each other.
    _template(session, scale, owner=other.user_id, template_type="user")

    # But one owner cannot hold the same name twice.
    with pytest.raises(IntegrityError):
        _template(session, scale, owner=owner.user_id, template_type="user")


def test_templates_share_one_type_scale_many_to_one(session: Session) -> None:
    scale = _scale(session)
    standard = _template(session, scale, launch_set_key="standard")
    dark = _template(session, scale, name="Dark", launch_set_key="dark")

    assert standard.type_scale is scale
    assert set(scale.color_templates) == {standard, dark}


def test_formatting_rules_read_back_in_evaluation_order(session: Session) -> None:
    template = _template(session, _scale(session))
    second = _rule(session, template, 2, effect="rowText")
    first = _rule(session, template, 1)
    session.expire(template)

    # First-match-wins: the relationship IS the evaluation order.
    assert template.formatting_rules == [first, second]


def test_one_evaluation_slot_per_template_among_live_rules(session: Session) -> None:
    scale = _scale(session)
    template = _template(session, scale)
    other = _template(session, scale, name="Dark")
    _rule(session, template, 1)
    # The same slot on ANOTHER template is fine — ordering is per theme.
    _rule(session, other, 1)

    with pytest.raises(IntegrityError):
        _rule(session, template, 1)


def test_rule_requires_a_live_template_row(session: Session) -> None:
    rule = ConditionalFormattingRule(
        color_template_id=uuid.uuid4(),
        condition_field="status",
        condition_operator="isEmpty",
        effect="accent",
        effect_slot="statusWarning",
        evaluation_order=1,
    )
    session.add(rule)
    with pytest.raises(IntegrityError):
        session.flush()


def test_rule_persists_a_status_slot_never_a_literal_color(session: Session) -> None:
    # FND-906/REQ-045: the rule's applied color is the effectSlot NAME — the
    # effectColor hex column is gone, so a literal color has nowhere to live.
    rule = _rule(session, _template(session, _scale(session)), 1)

    assert rule.effect_slot == "statusNegative"
    assert "effectSlot" in ConditionalFormattingRule.__table__.columns
    assert "effectColor" not in ConditionalFormattingRule.__table__.columns


def test_layering_is_three_fixed_layers_with_the_row_theme_on_the_view() -> None:
    # FND-907/REQ-044/REQ-018: no per-grid override entity and no numeric
    # precedence — the third layer is the ACTIVE VIEW's own rowTheme document,
    # which already lives on gridView (WTK-041); the storage design carries
    # nothing else that could reorder or shadow the fixed positional stack.
    assert not hasattr(storage, "RowThemeOverride")
    assert "rowThemeOverride" not in storage.Base.metadata.tables
    assert "layerPrecedence" not in ColorTemplate.__table__.columns
    assert "rowTheme" in GridView.__table__.columns
