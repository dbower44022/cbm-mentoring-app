"""``GET/PUT /preferences/{key}`` — the one preference-persistence mechanism (REQ-060).

All view/pin/layout/filter/startup persistence rides this single pair over the
``userPreference`` table (DB-S13): org-wide defaults are rows with null
``userID``; the caller's own row overrides at read time. A new grid feature
needs a new ``preferenceKey``, never a new table, column, or endpoint.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.envelope import Envelope, field_error, ok
from mentorapp.api.errors import ApiValidationError, RecordNotFoundError
from mentorapp.storage import UserPreference, utcnow

router = APIRouter()

CODE_VALUE_TOO_LONG = "valueTooLong"

# The model declares String(200); SQLite (tests) does not enforce length, so
# the API does — one behavior regardless of backend, and GET and PUT agree on
# what a well-formed key is.
_MAX_KEY_LENGTH = 200


def _validate_key(preference_key: str) -> None:
    if len(preference_key) > _MAX_KEY_LENGTH:
        raise ApiValidationError(
            [
                field_error(
                    "preferenceKey",
                    CODE_VALUE_TOO_LONG,
                    f"preferenceKey must be at most {_MAX_KEY_LENGTH} characters.",
                )
            ]
        )


def _payload(row: UserPreference) -> dict[str, Any]:
    # preferenceScope tells the client whether it is looking at its own saved
    # state or the organization default it would override on first save.
    return {
        "preferenceKey": row.preference_key,
        "preferenceValue": row.preference_value,
        "preferenceScope": "orgDefault" if row.user_id is None else "user",
    }


def _user_row(
    session: Session, preference_key: str, user_id: uuid.UUID
) -> UserPreference | None:
    return session.scalars(
        select(UserPreference)
        .where(UserPreference.deleted_at.is_(None))
        .where(UserPreference.preference_key == preference_key)
        .where(UserPreference.user_id == user_id)
    ).first()


@router.get("/preferences/{preference_key}")
def get_preference(
    preference_key: str,
    session: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Envelope:
    """Resolve one preference document: the caller's own row, else the org default.

    Returns ``data.preferenceValue`` plus ``data.preferenceScope``
    (``user``/``orgDefault``). 404 when neither row exists — an unset
    preference is a normal state the client answers with its built-in default.
    """
    _validate_key(preference_key)
    rows = session.scalars(
        select(UserPreference)
        .where(UserPreference.deleted_at.is_(None))
        .where(UserPreference.preference_key == preference_key)
        .where(or_(UserPreference.user_id == user_id, UserPreference.user_id.is_(None)))
    ).all()
    row = next((r for r in rows if r.user_id == user_id), None)
    row = row or next((r for r in rows if r.user_id is None), None)
    if row is None:
        raise RecordNotFoundError("preference", preference_key)
    return ok(data=_payload(row))


class PreferencePutBody(BaseModel):
    """PUT body: the whole preference document — a partial merge does not exist."""

    preference_value: dict[str, Any] = Field(alias="preferenceValue")


@router.put("/preferences/{preference_key}")
def put_preference(
    preference_key: str,
    body: PreferencePutBody,
    session: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Envelope:
    """Create or replace the CALLER's preference document for one key.

    Always writes the user's own row — the org default is read-time fallback,
    never a write target here. Deliberately last-write-wins with no
    ``rowVersion`` round-trip: GET may have served the ORG-DEFAULT row while
    PUT targets the user's row, so a version carried across that scope
    boundary is incoherent — and the document is opaque client state (layouts,
    pins) with no field-level merge a 409 dialog could offer; the newest save
    is the right one. An unchanged document is a no-op (no version bump).
    """
    _validate_key(preference_key)
    row = _user_row(session, preference_key, user_id)
    if row is None:
        row = UserPreference(
            user_id=user_id,
            preference_key=preference_key,
            preference_value=body.preference_value,
            created_by=user_id,
            modified_by=user_id,
        )
        session.add(row)
    elif row.preference_value != body.preference_value:
        row.preference_value = body.preference_value
        row.modified_by = user_id
        row.modified_at = utcnow()
    session.commit()
    return ok(data=_payload(row))
