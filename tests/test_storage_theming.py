"""Tests for the look-and-feel data model (WTK-111, PI-007)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mentorapp.storage import (
    COLOR_SLOTS,
    CONDITION_OPERATORS,
    CONTRAST_GUARDRAIL_BEHAVIORS,
    FONT_SLOTS,
    FORMATTING_EFFECTS,
    LAUNCH_SETS,
    TEMPLATE_TYPES,
    TYPE_SCALE_STEPS,
    AppUser,
    ColorTemplate,
    ConditionalFormattingRule,
    Grid,
    RowThemeOverride,
    TypeScale,
)


def _user(session: Session, username: str = "mentor.one") -> AppUser:
    user = AppUser(crm_user_id=f"crm-{username}", username=username)
    session.add(user)
    session.flush()
    return user


def _grid(session: Session, key: str = "mentorRoster") -> Grid:
    grid = Grid(grid_key=key, grid_name="Mentor roster")
    session.add(grid)
    session.flush()
    return grid


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
        effect_color="#cc0000",
        evaluation_order=order,
    )
    session.add(rule)
    session.flush()
    return rule


def test_vocabularies_match_the_standard() -> None:
    assert TEMPLATE_TYPES == ("system", "user")
    assert LAUNCH_SETS == ("standard", "compact", "largePrint", "dark")
    assert COLOR_SLOTS == (
        "rowBackground",
        "alternateRowBackground",
        "rowText",
        "selectedRowBackground",
        "selectedRowText",
        "accent",
    )
    assert FONT_SLOTS == ("rowFont", "headerFont")
    assert TYPE_SCALE_STEPS == ("xs", "sm", "md", "lg", "xl")
    # PI-007: the guardrail educates — warns, never blocks.
    assert CONTRAST_GUARDRAIL_BEHAVIORS == ("warn",)
    # The effect enum is limited to fixed slots (WTK-111).
    assert set(FORMATTING_EFFECTS) <= set(COLOR_SLOTS)
    assert "equals" in CONDITION_OPERATORS


def test_template_defaults_speak_the_ruled_contract(session: Session) -> None:
    template = _template(session, _scale(session))

    assert template.type_step_choice == "md"
    assert template.contrast_guardrail_behavior == "warn"
    assert template.layer_precedence == 0
    assert template.launch_set_key is None
    assert template.row_version == 1


def test_type_scale_name_unique_among_live_rows(session: Session) -> None:
    first = _scale(session)

    with pytest.raises(IntegrityError):
        _scale(session)
    session.rollback()

    session.add(first)
    first.soft_delete()
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
    first.soft_delete()
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
        effect_color="#cc0000",
        evaluation_order=1,
    )
    session.add(rule)
    with pytest.raises(IntegrityError):
        session.flush()


def test_row_theme_override_is_at_most_one_per_grid(session: Session) -> None:
    scale = _scale(session)
    template = _template(session, scale)
    grid = _grid(session)
    override = RowThemeOverride(
        grid_id=grid.grid_id, color_template_id=template.color_template_id
    )
    session.add(override)
    session.flush()

    duplicate = RowThemeOverride(
        grid_id=grid.grid_id, color_template_id=template.color_template_id
    )
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    # Soft-deleting the live override frees the grid for a new one (DB-S3).
    session.add_all([scale, template, grid, override])
    override.soft_delete()
    session.flush()
    replacement = RowThemeOverride(
        grid_id=grid.grid_id, color_template_id=template.color_template_id
    )
    session.add(replacement)
    session.flush()

    live = session.scalars(
        select(RowThemeOverride).where(RowThemeOverride.deleted_at.is_(None))
    ).all()
    assert live == [replacement]
