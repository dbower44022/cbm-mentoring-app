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

Entity-type resolution follows the home-router seam pattern (fail loudly
until wired; tests and deployments override :func:`get_record_catalog`):
domain tables land with their own planning items, and an empty in-process
default would turn every preview into a silent 404.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.envelope import Envelope, field_error, ok
from mentorapp.api.errors import ApiValidationError, RecordNotFoundError
from mentorapp.api.records import serialize_record
from mentorapp.observability import get_logger
from mentorapp.ui.auth_flows import EducateMessage
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


_SessionDep = Annotated[Session, Depends(get_session)]
_UserDep = Annotated[uuid.UUID, Depends(get_current_user_id)]
_CatalogDep = Annotated[RecordCatalog, Depends(get_record_catalog)]


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
