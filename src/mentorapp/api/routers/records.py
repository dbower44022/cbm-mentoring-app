"""``/records`` — the record-window read surface: docked preview & pop-outs (WTK-029).

The build of the WTK-021 design (REQ-012): no frontend shell exists yet
(PI-002), so ``mentorapp.ui.record_preview`` stays the one home for the
window *behavior* (:class:`~mentorapp.ui.record_preview.RecordWindows`, the
pane and action declarations) and this router serves the *content* every
record window renders — the docked preview re-fetches it on each focus
change, every pop-out fetches it for its pinned record, and the
same-user-sync fan-out re-fetches it after a save in another window.

- ``GET /records/{entityType}/{recordId}/preview`` is the ONE content
  answer for both hosts. ``data.pane`` and ``data.popOutFrame`` carry the
  WTK-021 declarations verbatim (read-optimized, zero edit controls, the two
  edit paths; a real browser window with the header minus navigation) so a
  shell can never render a preview that drifts from the design.
- The record rides flat in ``data.record`` via
  :func:`~mentorapp.api.records.serialize_record` — built-ins and custom
  attributes indistinguishable (DB-R3), ``rowVersion`` included so any read
  can lead straight to an edit (DB-S4).
- **A soft-deleted record still answers** (200 + ``data.notice``): a pop-out
  is PINNED to its record, so a record removed while its window is open must
  explain itself in educate voice — honest soft-delete wording, never a
  blank window and never a 404 that reads as "the app broke". Only a record
  that never existed is 404.

The write half (REL-004 block 1) exposes the PI-004/PI-008 write engine over
the same catalog seam — the endpoints every form commits through:

- ``POST /records/{entityType}`` is the whole-record create
  (:func:`~mentorapp.api.write_engine.create_record`): registry-validated
  with every failure at once, duplicate-detected server-side (409 with the
  candidate records unless the recorded override rides along — REQ-037/059).
- ``PATCH /records/{entityType}/{recordId}`` is the one update verb
  (:func:`~mentorapp.api.write_engine.partial_update`): changed fields plus
  ``rowVersion`` flat in the body — exactly the
  :func:`~mentorapp.api.field_edit.single_field_patch` shape, so the
  per-field window and the full form speak one contract (DB-S12).
- ``POST /records/{entityType}/{recordId}/restore`` commits the REQ-037
  restore-instead-of-create choice under the standard ``rowVersion`` guard.
- ``POST /records/{entityType}/similar-records`` is the advisory pre-save
  check (deleted-inclusive, never blocking): the create form's
  similar-records offer reads its candidates here, from the SAME rule
  evaluation the create-time rejection uses.

Entity-type resolution follows the home-router seam pattern (fail loudly
until wired; tests and deployments override :func:`get_record_catalog`):
domain tables land with their own planning items, and an empty in-process
default would turn every preview into a silent 404.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from mentorapp.access.lookup_grants import LookupSourceResolver
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.edit_safety import ROW_VERSION_FIELD
from mentorapp.api.envelope import Envelope, field_error, ok
from mentorapp.api.errors import ApiValidationError, RecordNotFoundError
from mentorapp.api.lookup_suggestions import suggest_related_records
from mentorapp.api.records import registry_for, serialize_record
from mentorapp.api.routers.workprocess import RoleSource, get_role_source
from mentorapp.api.write_engine import (
    CODE_TYPE_MISMATCH,
    CODE_UNKNOWN_FIELD,
    create_record,
    find_similar_records,
    partial_update,
    restore_record,
)
from mentorapp.observability import get_logger
from mentorapp.ui.auth_flows import EducateMessage
from mentorapp.ui.lookup_control import related_entity_type
from mentorapp.ui.record_preview import (
    POP_OUT_HAS_NAVIGATION,
    POP_OUT_HEADER_RIGHT,
    RECORD_PREVIEW,
    PopOutWindow,
)

log = get_logger(__name__)

router = APIRouter()

CODE_UNKNOWN_ENTITY_TYPE = "unknownEntityType"

# The pop-out frame kind comes from the PopOutWindow declaration's default —
# one canonical "real browser window" value, never a second string literal.
_POP_OUT_KIND = PopOutWindow.__dataclass_fields__["kind"].default


class RecordCatalog(Protocol):
    """Resolve a wire entity-type name to its ORM entity class.

    The seam exists so this router never owns an entity table of its own —
    the catalog is wired once when the domain entities' planning items land,
    exactly as the home router's grant catalog is.
    """

    def entity_class(self, entity_type: str) -> type[Any] | None:
        """The entity's declarative class, or ``None`` for an unknown name."""
        ...


def get_record_catalog() -> RecordCatalog:
    """Provide the entity catalog; wiring binds it, tests override it.

    Fail-loud, never an empty default: a missing binding must read as a
    deployment error, not as every record in the app being gone.
    """
    raise RuntimeError(
        "record catalog provider is not wired; install records wiring or "
        "override get_record_catalog."
    )


def get_lookup_sources() -> LookupSourceResolver:
    """Provide the lookup-binding resolver (REQ-036); wiring binds it.

    Fail-loud like the catalog: an unbound resolver must read as a
    deployment error, never as "no entity has a lookup source" — that
    silent shape would render every relationship field noAccess.
    """
    raise RuntimeError(
        "lookup source resolver is not wired; install records wiring or "
        "override get_lookup_sources."
    )


_SessionDep = Annotated[Session, Depends(get_session)]
_UserDep = Annotated[uuid.UUID, Depends(get_current_user_id)]
_CatalogDep = Annotated[RecordCatalog, Depends(get_record_catalog)]
_RolesDep = Annotated[RoleSource, Depends(get_role_source)]
_LookupSourcesDep = Annotated[LookupSourceResolver, Depends(get_lookup_sources)]


def _entity_class(catalog: RecordCatalog, entity_type: str) -> type[Any]:
    """Resolve a wire entity-type name, or refuse with the standard 422."""
    entity_cls = catalog.entity_class(entity_type)
    if entity_cls is None:
        raise ApiValidationError(
            [
                field_error(
                    "entityType",
                    CODE_UNKNOWN_ENTITY_TYPE,
                    f"'{entity_type}' is not an entity type this app serves; "
                    "check the link or view that led here.",
                )
            ]
        )
    return entity_cls


def removed_record_message(entity_type: str) -> EducateMessage:
    """Educate-voice notice for a preview of a soft-deleted record.

    Honest soft-delete wording (grid standard): removed from views and
    restorable by an administrator — never "deleted" or "cannot be undone".
    """
    return EducateMessage(
        what_happened="This record has been removed.",
        why=(
            f"Someone removed this {entity_type} record, so it no longer "
            "appears in any view. Removed records are kept, not destroyed."
        ),
        what_next=(
            "You can still read it here. If it should come back, an "
            "administrator can restore it."
        ),
    )


def _pane_payload() -> dict[str, Any]:
    return {
        "dockPosition": RECORD_PREVIEW.dock_position,
        "dockedWhen": RECORD_PREVIEW.docked_when,
        "readOptimized": RECORD_PREVIEW.read_optimized,
        "editControls": RECORD_PREVIEW.edit_controls,
        "editPaths": list(RECORD_PREVIEW.edit_paths),
    }


def _pop_out_frame_payload() -> dict[str, Any]:
    return {
        "kind": _POP_OUT_KIND,
        "hasNavigation": POP_OUT_HAS_NAVIGATION,
        "headerRight": list(POP_OUT_HEADER_RIGHT),
    }


@router.get("/records/{entity_type}/{record_id}/preview")
def get_record_preview(
    entity_type: str,
    record_id: uuid.UUID,
    session: _SessionDep,
    user_id: _UserDep,
    catalog: _CatalogDep,
) -> Envelope:
    """The read-optimized content one record window renders (REQ-012).

    Serves the flat record (custom attributes merged, ``rowVersion``
    included) plus the WTK-021 pane and pop-out-frame declarations. 422
    ``unknownEntityType`` for a name the catalog does not know; 404 for a
    record that never existed; 200 with ``data.notice`` for a soft-deleted
    one (a pinned pop-out must explain, never blank). Fails 500 when the
    catalog provider is unwired; 401 without a live session reference
    (FND-909 D9).
    """
    entity_cls = _entity_class(catalog, entity_type)
    record = session.get(entity_cls, record_id)
    if record is None:
        raise RecordNotFoundError(entity_type, str(record_id))
    notice: EducateMessage | None = None
    if record.deleted_at is not None:
        notice = removed_record_message(entity_type)
        log.info(
            "preview served for a soft-deleted record",
            extra={
                "context": {
                    "userId": str(user_id),
                    "entityType": entity_type,
                    "recordId": str(record_id),
                }
            },
        )
    return ok(
        data={
            "pane": _pane_payload(),
            "popOutFrame": _pop_out_frame_payload(),
            "record": serialize_record(record),
            "notice": notice.as_payload() if notice else None,
        }
    )


# --- The write surface (REL-004 block 1: REQ-032/035/037 commits land here) ---------


class RecordCreateBody(BaseModel):
    """POST body — the :class:`~mentorapp.api.record_create.CommitCreate` shape.

    ``overrideDuplicates`` is set ONLY when the user chose Continue past the
    server's duplicate rejection; the engine records the override (REQ-059).
    """

    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]
    override_duplicates: bool = Field(default=False, alias="overrideDuplicates")
    override_reason: str | None = Field(default=None, alias="overrideReason")


class SimilarRecordsBody(BaseModel):
    """The advisory check's input: whatever identity fields the form holds so far."""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]


class RestoreBody(BaseModel):
    """Restore commits under the candidate's ``rowVersion``, like every write."""

    model_config = ConfigDict(extra="forbid")

    row_version: int = Field(alias="rowVersion")


@router.post("/records/{entity_type}")
def post_record(
    entity_type: str,
    body: RecordCreateBody,
    session: _SessionDep,
    user_id: _UserDep,
    catalog: _CatalogDep,
) -> Envelope:
    """Whole-record create through the one write engine (REQ-037, DB-S12).

    Registry-validated with every field failure in one round trip; duplicate
    detection answers 409 with the candidate records unless the body carries
    the user's recorded override. Returns the created record flat (with
    ``rowVersion``) so the form can land on the read view without a second
    fetch.
    """
    entity_cls = _entity_class(catalog, entity_type)
    record = create_record(
        session,
        entity_cls,
        entity_type,
        body.values,
        acting_user_id=user_id,
        override_duplicates=body.override_duplicates,
        override_reason=body.override_reason,
    )
    session.commit()
    log.info(
        "record created",
        extra={
            "context": {
                "userId": str(user_id),
                "entityType": entity_type,
                "overrodeDuplicates": body.override_duplicates,
            }
        },
    )
    return ok(data={"record": serialize_record(record)})


@router.patch("/records/{entity_type}/{record_id}")
def patch_record(
    entity_type: str,
    record_id: uuid.UUID,
    body: dict[str, Any],
    session: _SessionDep,
    user_id: _UserDep,
    catalog: _CatalogDep,
) -> Envelope:
    """PATCH — changed fields plus ``rowVersion``, flat (DB-S4/S12).

    The body is exactly :func:`~mentorapp.api.field_edit.single_field_patch`
    output scaled to any field count: the full edit form and the per-field
    window speak this one contract. A stale ``rowVersion`` is the standard
    409 carrying the current record; a no-op payload returns the record
    untouched (no version bump). A soft-deleted target is 404 — editing a
    removed record is a restore first, never a silent resurrection.
    """
    row_version = body.pop(ROW_VERSION_FIELD, None)
    if not isinstance(row_version, int) or isinstance(row_version, bool):
        raise ApiValidationError(
            [
                field_error(
                    ROW_VERSION_FIELD,
                    CODE_TYPE_MISMATCH,
                    "Every update carries the rowVersion the edit was based on.",
                )
            ]
        )
    entity_cls = _entity_class(catalog, entity_type)
    record = session.get(entity_cls, record_id)
    if record is None:
        raise RecordNotFoundError(entity_type, str(record_id))
    updated = partial_update(
        session,
        record,
        entity_type,
        body,
        row_version=row_version,
        acting_user_id=user_id,
    )
    session.commit()
    log.info(
        "record updated",
        extra={
            "context": {
                "userId": str(user_id),
                "entityType": entity_type,
                "recordId": str(record_id),
                "fieldCount": len(body),
            }
        },
    )
    return ok(data={"record": serialize_record(updated)})


@router.post("/records/{entity_type}/{record_id}/restore")
def post_record_restore(
    entity_type: str,
    record_id: uuid.UUID,
    body: RestoreBody,
    session: _SessionDep,
    user_id: _UserDep,
    catalog: _CatalogDep,
) -> Envelope:
    """Bring a removed record back — the restore-instead-of-create write (REQ-037).

    Restoring a live record is a no-op returning it unchanged (the offer
    raced a restore that already happened — not an error). A stale
    ``rowVersion`` is the standard 409 with the current record.
    """
    entity_cls = _entity_class(catalog, entity_type)
    record = session.get(entity_cls, record_id)
    if record is None:
        raise RecordNotFoundError(entity_type, str(record_id))
    restored = restore_record(
        session,
        record,
        entity_type,
        row_version=body.row_version,
        acting_user_id=user_id,
    )
    session.commit()
    log.info(
        "record restored",
        extra={
            "context": {
                "userId": str(user_id),
                "entityType": entity_type,
                "recordId": str(record_id),
            }
        },
    )
    return ok(data={"record": serialize_record(restored)})


@router.post("/records/{entity_type}/similar-records")
def post_similar_records(
    entity_type: str,
    body: SimilarRecordsBody,
    session: _SessionDep,
    user_id: _UserDep,
    catalog: _CatalogDep,
) -> Envelope:
    """The advisory pre-save duplicate check — compare, never block (REQ-037).

    Runs the SAME rule evaluation as the create-time rejection
    (:func:`~mentorapp.api.write_engine.find_similar_records`), deleted-
    inclusive so a match on a removed record can offer restore instead of
    create. ``data.candidates`` carries each match flat with ``removed``
    beside it; ``data.blocking`` is declared false so no shell can render
    this offer as a wall in front of Save.
    """
    entity_cls = _entity_class(catalog, entity_type)
    registry = registry_for(session, entity_type)
    candidates, matched_rule_names = find_similar_records(
        session, entity_cls, registry, body.values, include_deleted=True
    )
    if candidates:
        log.info(
            "similar-records offer served",
            extra={
                "context": {
                    "userId": str(user_id),
                    "entityType": entity_type,
                    "candidateCount": len(candidates),
                    "matchedRuleNames": matched_rule_names,
                }
            },
        )
    return ok(
        data={
            "candidates": [
                {
                    "record": serialize_record(record),
                    "removed": record.deleted_at is not None,
                }
                for record in candidates
            ],
            "matchedRuleNames": matched_rule_names,
            "blocking": False,
        },
        meta={"candidateCount": len(candidates)},
    )


@router.get("/lookups/{entity_type}/{field_name}")
def get_lookup_suggestions(
    entity_type: str,
    field_name: str,
    session: _SessionDep,
    user_id: _UserDep,
    catalog: _CatalogDep,
    roles: _RolesDep,
    lookup_sources: _LookupSourcesDep,
    q: str = "",
) -> Envelope:
    """One type-ahead keystroke for a relationship field (REQ-036).

    ``entity_type``/``field_name`` name the HOST entity's reference field
    (its registry row supplies the label the educate states speak);
    the related entity is derived from the entity-named key itself
    (``mentorID`` → ``mentor``, DB-R2b). The whole outcome — matches with
    the full-set count, keep-typing, or a no-access explanation that keeps
    the field visible — comes from the one suggestion read; access denial
    is a rendered phase here, never an HTTP error, because the control
    educates instead of hiding.
    """
    host_registry = registry_for(session, entity_type)
    field_row = host_registry.get(field_name)
    if field_row is None:
        raise ApiValidationError(
            [
                field_error(
                    "fieldName",
                    CODE_UNKNOWN_FIELD,
                    f"'{entity_type}' has no field named '{field_name}'.",
                )
            ]
        )
    try:
        related = related_entity_type(field_name)
    except ValueError:
        raise ApiValidationError(
            [
                field_error(
                    "fieldName",
                    CODE_UNKNOWN_FIELD,
                    f"'{field_name}' is not a relationship field; lookups "
                    "search the data set an entity-named key references.",
                )
            ]
        ) from None
    related_cls = _entity_class(catalog, related)
    outcome = suggest_related_records(
        session,
        lookup_sources,
        entity_cls=related_cls,
        field_name=field_name,
        search_text=q,
        user_id=user_id,
        user_roles=roles.user_roles(user_id),
        related_label=field_row.field_label,
    )
    return ok(
        data={
            "phase": outcome.phase,
            "suggestions": [
                {
                    "entityType": ref.entity_type,
                    "recordId": ref.record_id,
                    "title": ref.title,
                }
                for ref in outcome.suggestions
            ],
            "totalMatches": outcome.total_matches,
            "summary": outcome.summary,
            "message": outcome.message.as_payload() if outcome.message else None,
        }
    )
