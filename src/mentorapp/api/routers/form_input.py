"""``/form-input`` — smart formatting, paste resolution, postal auto-fill (REQ-034).

The thin HTTP skin over :mod:`mentorapp.api.form_input`, wired now that form
screens exist (REL-004 block 1). The parsers behind paste resolution are the
SAME ones that feed the duplicate-match shadow columns
(``automation.normalization``, DB-S13) — serving them over HTTP keeps one
canonical home instead of a drifting TypeScript re-implementation.

All three answers are conveniences, never gates: an unformattable value comes
back as typed, an unconfident paste keeps its remainder visible, an unknown
postal code answers ``null`` (unknown, not invalid).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.envelope import Envelope, ok
from mentorapp.api.form_input import auto_format, postal_autofill, resolve_paste

# The answers read nothing user-scoped, but the surface is authenticated like
# every other (D9): the dependency runs for its refusal, not its value.
router = APIRouter(dependencies=[Depends(get_current_user_id)])

_SessionDep = Annotated[Session, Depends(get_session)]


class FormatBody(BaseModel):
    """One focus-exit value: the registry ``fieldType`` decides the formatter."""

    model_config = ConfigDict(extra="forbid")

    field_type: str = Field(alias="fieldType")
    value: str


class PasteBody(BaseModel):
    """One composite-field paste: free text in, components + remainder out."""

    model_config = ConfigDict(extra="forbid")

    field_type: str = Field(alias="fieldType")
    text: str


@router.post("/form-input/format")
def post_format(body: FormatBody) -> Envelope:
    """Auto-format one typed value (REQ-034) — convenience, never a gate."""
    return ok(data={"value": auto_format(body.field_type, body.value)})


@router.post("/form-input/resolve-paste")
def post_resolve_paste(body: PasteBody) -> Envelope:
    """Resolve pasted free text into components (REQ-034).

    Confident components fill; ``remainder`` MUST stay visible in the
    pasted-into control — never discarded, and the paste is never blocked.
    """
    resolution = resolve_paste(body.field_type, body.text)
    return ok(data={"components": resolution.components, "remainder": resolution.remainder})


@router.get("/form-input/postal-autofill")
def get_postal_autofill(
    session: _SessionDep,
    postal_code: str = "",
    country_code: str = "US",
) -> Envelope:
    """City/state for a postal code (REQ-034 auto-fill over the REQ-061 rows).

    ``data.fill`` is ``{cityName, stateCode}`` or ``null`` — unknown, not
    invalid; the form fills EMPTY controls only, never what the user typed.
    """
    fill = postal_autofill(session, postal_code, country_code=country_code)
    return ok(data={"fill": fill})
