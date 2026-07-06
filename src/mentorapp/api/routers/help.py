"""``/help`` — page → URL resolution and the admin configuration CRUD (WTK-100).

REQ-043 over the wire, per the help-system standard (SKL-116): help content
lives OUTSIDE the app on the organization's docs platform, so the only thing
this router ever serves is a URL to open (in a separate browser tab — the
client's contract) and, when the answer is generic, the educate-voice notice
explaining the landing. Nothing here is ever a dead link and nothing is ever
hidden: every resolve request gets an answer.

``GET /help/resolve`` is the ONE resolution path every Help affordance calls
(the floating icon, the menus' last-item Help, the workprocess frame's
per-step Help — SKL-122): the walk is mapping row → configured URL pattern →
help home, in that order:

- A live mapping row answers its URL — page-specific, no notice.
- No row but a configured ``defaultURLPattern`` answers the pattern with the
  request's own coordinates substituted — still page-specific (the docs
  platform organizes by the same coordinates the mapping speaks), so
  ``mapped`` is ``True`` and there is no notice. The truthful row-vs-pattern
  distinction rides ``meta.resolution`` for anyone who needs it; ``mapped``
  deliberately means "page-specific", not "a row exists", because the one
  decision clients make is whether to explain a generic landing.
- Neither → the help home with the REQ-043 educate note ("no page-specific
  help exists yet"), ``mapped`` ``False``.
- Nothing configured at all → ``url`` ``null`` with a notice saying help
  isn't set up yet: the client surfaces the explanation instead of opening
  a window — an unconfigured help system explains itself, it never 500s and
  never opens a blank tab.

Resolution is every signed-in user's read (no capability — Help is never
hidden); the mapping and settings CRUD is the Administrator persona's act
behind ``help.admin`` (:mod:`mentorapp.access.help`). Envelope + structured
errors per the house write contract (DB-S12, DB-S4).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.access.help import authorize_stored_help_administration
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.envelope import ApiError, Envelope, field_error, ok
from mentorapp.api.errors import ApiValidationError, RecordNotFoundError, StaleRowVersionError
from mentorapp.observability import get_logger
from mentorapp.storage import (
    HELP_SOURCE_TYPES,
    HelpMapping,
    HelpSettings,
    help_settings,
    live_help_mapping,
    utcnow,
)
from mentorapp.storage.help import (
    PATTERN_SOURCE_IDENTIFIER_PLACEHOLDER,
    PATTERN_SOURCE_TYPE_PLACEHOLDER,
)

log = get_logger(__name__)

router = APIRouter()

_MAPPING_ENTITY = "helpMapping"

# Stable machine-readable codes (clients switch on these; adding is additive).
CODE_UNKNOWN_HELP_SOURCE_TYPE = "unknownHelpSourceType"
CODE_DUPLICATE_HELP_MAPPING = "duplicateHelpMapping"
CODE_INVALID_HELP_URL = "invalidHelpURL"
CODE_PATTERN_WITHOUT_PLACEHOLDER = "patternWithoutPlaceholder"

# How the resolve answer was produced — served in meta.resolution so the
# row-vs-pattern fact stays truthful even though both present as mapped.
RESOLUTION_MAPPING = "mapping"
RESOLUTION_PATTERN = "pattern"
RESOLUTION_HOME = "home"
RESOLUTION_UNCONFIGURED = "unconfigured"

# The educate voice for a generic landing (REQ-043's wording) — what happened
# and who can improve it, never a bare failure. The surface noun follows the
# source type so a data set's Help doesn't call itself a panel.
_SURFACE_NOUNS = {"panel": "panel", "dataSet": "data set", "workprocess": "workprocess"}


def _unmapped_notice(source_type: str) -> str:
    return (
        f"No page-specific help exists yet for this {_SURFACE_NOUNS[source_type]} — "
        f"this opens the help site's home. An administrator can map this page to a "
        f"specific help article."
    )


_UNCONFIGURED_NOTICE = (
    "Help isn't set up yet: no help site is configured for this app. An "
    "administrator configures the help home and page mappings."
)


_SessionDep = Annotated[Session, Depends(get_session)]
_UserDep = Annotated[uuid.UUID, Depends(get_current_user_id)]


# --- Payloads ------------------------------------------------------------------------


def _mapping_payload(mapping: HelpMapping) -> dict[str, Any]:
    return {
        "helpMappingID": mapping.help_mapping_id,
        "sourceType": mapping.source_type,
        "sourceIdentifier": mapping.source_identifier,
        "helpURL": mapping.help_url,
        "rowVersion": mapping.row_version,
    }


def _settings_payload(settings: HelpSettings) -> dict[str, Any]:
    return {
        "helpSettingsID": settings.help_settings_id,
        "helpHomeURL": settings.help_home_url,
        "defaultURLPattern": settings.default_url_pattern,
        "rowVersion": settings.row_version,
    }


# --- Shared validation ---------------------------------------------------------------


def _source_type_errors(source_type: str) -> list[ApiError]:
    if source_type in HELP_SOURCE_TYPES:
        return []
    return [
        field_error(
            "sourceType",
            CODE_UNKNOWN_HELP_SOURCE_TYPE,
            f"'{source_type}' is not a help surface kind; the kinds are "
            f"{', '.join(HELP_SOURCE_TYPES)}.",
        )
    ]


def _url_errors(field_name: str, value: str) -> list[ApiError]:
    # An absolute web URL only: help opens in a separate browser tab, so a
    # relative path or a bare word would be the dead link REQ-043 forbids.
    if value.startswith(("https://", "http://")):
        return []
    return [
        field_error(
            field_name,
            CODE_INVALID_HELP_URL,
            f"'{value}' is not an absolute web URL; help links start with "
            f"https:// (or http://).",
        )
    ]


def _pattern_errors(pattern: str) -> list[ApiError]:
    """The pattern's own gates — only judged when a pattern is present.

    Empty is sanctioned (it unconfigures the pattern). A non-empty pattern
    must be an absolute URL AND carry at least one placeholder: without one
    it would send every unmapped page to a single URL while presenting as
    page-specific help — that generic landing is the help HOME's job, and
    the home comes with the honest notice.
    """
    if pattern == "":
        return []
    errors = _url_errors("defaultURLPattern", pattern)
    if (
        PATTERN_SOURCE_TYPE_PLACEHOLDER not in pattern
        and PATTERN_SOURCE_IDENTIFIER_PLACEHOLDER not in pattern
    ):
        errors.append(
            field_error(
                "defaultURLPattern",
                CODE_PATTERN_WITHOUT_PLACEHOLDER,
                f"the pattern carries no placeholder; include "
                f"{PATTERN_SOURCE_TYPE_PLACEHOLDER} and/or "
                f"{PATTERN_SOURCE_IDENTIFIER_PLACEHOLDER} so unmapped pages "
                f"get page-specific URLs — a single fixed URL belongs in "
                f"helpHomeURL.",
            )
        )
    return errors


def _duplicate_mapping_errors(
    session: Session,
    source_type: str,
    source_identifier: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> list[ApiError]:
    query = (
        select(HelpMapping)
        .where(HelpMapping.deleted_at.is_(None))
        .where(HelpMapping.source_type == source_type)
        .where(HelpMapping.source_identifier == source_identifier)
    )
    if exclude_id is not None:
        query = query.where(HelpMapping.help_mapping_id != exclude_id)
    if session.scalars(query).first() is None:
        return []
    return [
        field_error(
            "sourceIdentifier",
            CODE_DUPLICATE_HELP_MAPPING,
            f"a live mapping for {source_type} '{source_identifier}' already "
            f"exists; edit that mapping instead — one page resolves to one URL.",
        )
    ]


# --- Resolution (every user's read, REQ-043) -----------------------------------------


def _pattern_url(pattern: str, source_type: str, source_identifier: str) -> str:
    # Values are URL-encoded into the path (identifiers may carry spaces —
    # workprocess names are display names); the pattern text itself is the
    # admin's URL and passes through untouched.
    return pattern.replace(
        PATTERN_SOURCE_TYPE_PLACEHOLDER, quote(source_type, safe="")
    ).replace(PATTERN_SOURCE_IDENTIFIER_PLACEHOLDER, quote(source_identifier, safe=""))


@router.get("/help/resolve")
def resolve(
    session: _SessionDep,
    user_id: _UserDep,
    source_type: Annotated[str, Query(alias="sourceType")],
    source_identifier: Annotated[str, Query(alias="sourceIdentifier", min_length=1)],
) -> Envelope:
    """Where does Help for this surface go? — the ONE resolution read.

    Walks mapping row → URL pattern → help home (see the module docstring
    for each answer's shape). ``mapped`` means "the URL is page-specific";
    ``notice`` is non-null exactly when the client should explain a generic
    landing; ``url`` is null only when help is entirely unconfigured, in
    which case the notice IS the answer. Unknown ``sourceType`` is the
    caller's mistake (422 educate), not an unmapped page.
    """
    errors = _source_type_errors(source_type)
    if errors:
        raise ApiValidationError(errors)
    mapping = live_help_mapping(
        session, source_type=source_type, source_identifier=source_identifier
    )
    settings = help_settings(session)
    if mapping is not None:
        url: str | None = mapping.help_url
        mapped, notice, resolution = True, None, RESOLUTION_MAPPING
    elif settings.default_url_pattern:
        url = _pattern_url(settings.default_url_pattern, source_type, source_identifier)
        mapped, notice, resolution = True, None, RESOLUTION_PATTERN
    elif settings.help_home_url:
        url = settings.help_home_url
        mapped, notice, resolution = False, _unmapped_notice(source_type), RESOLUTION_HOME
    else:
        url = None
        mapped, notice, resolution = False, _UNCONFIGURED_NOTICE, RESOLUTION_UNCONFIGURED
    log.info(
        "help resolved",
        extra={
            "context": {
                "sourceType": source_type,
                "sourceIdentifier": source_identifier,
                "resolution": resolution,
                "userID": str(user_id),
            }
        },
    )
    return ok(
        data={"url": url, "mapped": mapped, "notice": notice},
        meta={"resolution": resolution},
    )


# --- Mapping CRUD (admin-gated, REQ-043) ---------------------------------------------


def _live_mapping(session: Session, mapping_id: uuid.UUID) -> HelpMapping:
    mapping = session.get(HelpMapping, mapping_id)
    if mapping is None or mapping.deleted_at is not None:
        raise RecordNotFoundError(_MAPPING_ENTITY, str(mapping_id))
    return mapping


@router.get("/help/mappings")
def list_mappings(session: _SessionDep, user_id: _UserDep) -> Envelope:
    """The admin management list: every live mapping, surface-ordered."""
    authorize_stored_help_administration(session, user_id=user_id)
    mappings = session.scalars(
        select(HelpMapping)
        .where(HelpMapping.deleted_at.is_(None))
        .order_by(HelpMapping.source_type, HelpMapping.source_identifier)
    ).all()
    return ok(data=[_mapping_payload(m) for m in mappings])


class MappingCreateBody(BaseModel):
    """POST body: the whole mapping fact — which surface opens which URL."""

    model_config = ConfigDict(extra="forbid")

    source_type: str = Field(alias="sourceType")
    source_identifier: str = Field(alias="sourceIdentifier", min_length=1, max_length=200)
    help_url: str = Field(alias="helpURL", min_length=1, max_length=2000)


@router.post("/help/mappings")
def create_mapping(
    body: MappingCreateBody, session: _SessionDep, user_id: _UserDep
) -> Envelope:
    """Map one surface to one help URL (REQ-043) — the Administrator's act.

    Every gate in one round trip (DB-S12): vocabulary membership, the
    absolute-URL shape, and the one-live-mapping-per-surface rule.
    """
    authorize_stored_help_administration(session, user_id=user_id)
    errors = (
        _source_type_errors(body.source_type)
        + _url_errors("helpURL", body.help_url)
        + _duplicate_mapping_errors(session, body.source_type, body.source_identifier)
    )
    if errors:
        raise ApiValidationError(errors)
    mapping = HelpMapping(
        source_type=body.source_type,
        source_identifier=body.source_identifier,
        help_url=body.help_url,
        created_by=user_id,
        modified_by=user_id,
    )
    session.add(mapping)
    session.commit()
    log.info(
        "help mapping created",
        extra={
            "context": {
                "helpMappingID": str(mapping.help_mapping_id),
                "sourceType": body.source_type,
                "sourceIdentifier": body.source_identifier,
                "userID": str(user_id),
            }
        },
    )
    return ok(data=_mapping_payload(mapping))


class MappingPatchBody(BaseModel):
    """PATCH body: only the changed fields plus the mandatory ``rowVersion``."""

    model_config = ConfigDict(extra="forbid")

    row_version: int = Field(alias="rowVersion")
    source_type: str | None = Field(default=None, alias="sourceType")
    source_identifier: str | None = Field(
        default=None, alias="sourceIdentifier", min_length=1, max_length=200
    )
    help_url: str | None = Field(default=None, alias="helpURL", min_length=1, max_length=2000)


@router.patch("/help/mappings/{mapping_id}")
def patch_mapping(
    mapping_id: uuid.UUID,
    body: MappingPatchBody,
    session: _SessionDep,
    user_id: _UserDep,
) -> Envelope:
    """Per-field mapping edit under the write contract (DB-S12, DB-S4).

    The MERGED surface re-validates: retyping or re-identifying a mapping
    must not land on another mapping's live surface.
    """
    authorize_stored_help_administration(session, user_id=user_id)
    mapping = _live_mapping(session, mapping_id)
    if body.row_version != mapping.row_version:
        raise StaleRowVersionError(_mapping_payload(mapping))
    sent = body.model_fields_set
    merged_type = (
        body.source_type
        if "source_type" in sent and body.source_type is not None
        else mapping.source_type
    )
    merged_identifier = (
        body.source_identifier
        if "source_identifier" in sent and body.source_identifier is not None
        else mapping.source_identifier
    )
    errors = _source_type_errors(merged_type)
    if "help_url" in sent and body.help_url is not None:
        errors += _url_errors("helpURL", body.help_url)
    if not errors and ("source_type" in sent or "source_identifier" in sent):
        errors += _duplicate_mapping_errors(
            session, merged_type, merged_identifier, exclude_id=mapping_id
        )
    if errors:
        raise ApiValidationError(errors)
    for attr in ("source_type", "source_identifier", "help_url"):
        if attr in sent and getattr(body, attr) is not None:
            setattr(mapping, attr, getattr(body, attr))
    mapping.modified_by = user_id
    mapping.modified_at = utcnow()
    session.commit()
    return ok(data=_mapping_payload(mapping))


@router.delete("/help/mappings/{mapping_id}")
def delete_mapping(mapping_id: uuid.UUID, session: _SessionDep, user_id: _UserDep) -> Envelope:
    """Unmap a surface (DB-S3 soft delete): the very next Help click for that
    page falls through to the pattern/home walk. Retained, never deleted."""
    authorize_stored_help_administration(session, user_id=user_id)
    mapping = _live_mapping(session, mapping_id)
    now = utcnow()
    mapping.deleted_at = now
    mapping.deleted_by = user_id
    mapping.modified_at = now
    mapping.modified_by = user_id
    session.commit()
    log.info(
        "help mapping retired",
        extra={"context": {"helpMappingID": str(mapping_id), "userID": str(user_id)}},
    )
    return ok(data={"helpMappingID": mapping_id, "deleted": True})


# --- Settings (admin-gated singleton) ------------------------------------------------


@router.get("/help/settings")
def get_settings(session: _SessionDep, user_id: _UserDep) -> Envelope:
    """The ONE help-settings document (REQ-043), with its ``rowVersion`` —
    this read leads to the PATCH (DB-S4). Seeded by migration 0013, so an
    absent row is a broken deployment surfacing as the opaque 500."""
    authorize_stored_help_administration(session, user_id=user_id)
    return ok(data=_settings_payload(help_settings(session)))


class SettingsPatchBody(BaseModel):
    """PATCH body: the changed values plus the mandatory ``rowVersion``.

    Empty string is a sanctioned VALUE (it unconfigures that fallback), so
    ``None``/absent means "leave alone" and ``""`` means "clear" —
    ``model_fields_set`` distinguishes them.
    """

    model_config = ConfigDict(extra="forbid")

    row_version: int = Field(alias="rowVersion")
    help_home_url: str | None = Field(default=None, alias="helpHomeURL", max_length=2000)
    default_url_pattern: str | None = Field(
        default=None, alias="defaultURLPattern", max_length=2000
    )


@router.patch("/help/settings")
def patch_settings(
    body: SettingsPatchBody, session: _SessionDep, user_id: _UserDep
) -> Envelope:
    """Retune the fallback document: help home URL and/or the URL pattern.

    A non-empty home must be an absolute URL; a non-empty pattern must be an
    absolute URL carrying a placeholder (see :func:`_pattern_errors` for the
    WHY). Clearing either (empty string) is sanctioned — the resolve answer
    explains an unconfigured system instead of refusing.
    """
    authorize_stored_help_administration(session, user_id=user_id)
    settings = help_settings(session)
    if body.row_version != settings.row_version:
        raise StaleRowVersionError(_settings_payload(settings))
    sent = body.model_fields_set
    errors: list[ApiError] = []
    if "help_home_url" in sent and body.help_home_url:
        errors += _url_errors("helpHomeURL", body.help_home_url)
    if "default_url_pattern" in sent and body.default_url_pattern is not None:
        errors += _pattern_errors(body.default_url_pattern)
    if errors:
        raise ApiValidationError(errors)
    for attr in ("help_home_url", "default_url_pattern"):
        if attr in sent and getattr(body, attr) is not None:
            setattr(settings, attr, getattr(body, attr))
    if session.is_modified(settings):
        settings.modified_by = user_id
        settings.modified_at = utcnow()
        session.commit()
        log.info(
            "help settings updated",
            extra={
                "context": {
                    "helpSettingsID": str(settings.help_settings_id),
                    "userID": str(user_id),
                }
            },
        )
    return ok(data=_settings_payload(settings))
