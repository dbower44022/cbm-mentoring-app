"""Seed the four curated launch templates as system color templates (PI-013).

REQ-044's launch set — Standard, Compact, Large print, Dark — must exist as
system ``colorTemplate`` rows for the Themes picker to offer them and for
``GET /theming/effective`` layer one to resolve to a real row (the router's
in-code Standard fallback was the interim). Seeds each launch template from
its canonical document (``ui.theming.LAUNCH_TEMPLATES``) over the one shared
type scale, idempotent by reconstruction like ``seed_mentor_access``.

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.storage.theming import ColorTemplate, shared_type_scale
from mentorapp.ui.template_manager import FONT_WEIGHT_REGULAR
from mentorapp.ui.theming import LAUNCH_TEMPLATE_NAMES, LAUNCH_TEMPLATES

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def _font_slots(document: dict) -> dict:
    # The persisted slot shape: each font slot names the template's base step
    # (fonts pick a step, never a raw size — REQ-046), its family, and weight.
    step = document["sizeStep"]
    return {
        slot: {"stepKey": step, "fontFamily": family, "fontWeight": FONT_WEIGHT_REGULAR}
        for slot, family in document["fonts"].items()
    }


def _seed_launch_templates(session: Session) -> None:
    scale = shared_type_scale(session)
    for launch_key, document in LAUNCH_TEMPLATES.items():
        existing = session.scalars(
            select(ColorTemplate)
            .where(ColorTemplate.deleted_at.is_(None))
            .where(ColorTemplate.user_id.is_(None))
            .where(ColorTemplate.launch_set_key == launch_key)
        ).first()
        values = {
            "color_template_name": LAUNCH_TEMPLATE_NAMES[launch_key],
            "template_type": "system",
            "user_id": None,
            "type_scale_id": scale.type_scale_id,
            "color_slots": dict(document["colors"]),
            "font_slots": _font_slots(document),
            "type_step_choice": document["sizeStep"],
            "launch_set_key": launch_key,
        }
        if existing is None:
            session.add(ColorTemplate(**values))
        else:
            for attr, value in values.items():
                setattr(existing, attr, value)
    session.flush()


def upgrade() -> None:
    _seed_launch_templates(Session(bind=op.get_bind()))


def downgrade() -> None:
    # Remove exactly the seeded system launch templates (their user rows, if
    # any were copied from them, are independent user data and survive).
    from sqlalchemy import delete

    op.execute(
        delete(ColorTemplate.__table__).where(
            ColorTemplate.__table__.c.templateType == "system",
            ColorTemplate.__table__.c.launchSetKey.in_(tuple(LAUNCH_TEMPLATES)),
        )
    )
