"""StoredViewEnforcement: the WTK-044 rules bound to persisted rows (WTK-050).

:mod:`~mentorapp.access.views` decided REQ-017/REQ-019/REQ-028 as pure rules
over :class:`~mentorapp.access.views.ViewFacts`; this module is their stored
implementation — entry points that load the live ``gridView`` row, adapt it,
and decide in ONE call, so an endpoint cannot load a view and forget to
authorize the mutation it is about to make (the same one-entry-point shape as
:func:`~mentorapp.access.grants.run_stored_data_source`). Each authorizer
returns the row it authorized: the caller mutates exactly what was checked,
never a second lookup that could race the first.

Deep-link resolution (REQ-028) splits across the layer boundary on purpose:
the pure rule is ``mentorapp.api.grid_surface.resolve_grid_link`` — which
access (rank 2) cannot import — and the storage half that rule deliberately
left outside itself is :func:`load_deep_link_facts` here: one lookup that
turns a ``gridDeepLink`` key into exactly the facts the resolver consumes —
the grid key, the named view with its owner, and the REQ-006 data-source
permission fact through :class:`~mentorapp.access.grants.StoredGrantRegistry`
(one grant boundary, never a second). The endpoint composes the two; the
recipient's last-used view is the API's own preference read (FND-018).

Every lookup is live-rows-only (DB-S3), which is what makes revocation and
retirement need no sweep: soft-deleting a view, its data source, or a grant
changes the very next decision, because every decision re-reads.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.access.grants import StoredGrantRegistry, roles_cover_data_source
from mentorapp.access.views import (
    StoredCapabilityRegistry,
    ViewFacts,
    authorize_data_source_authoring,
    authorize_view_management,
    authorize_view_promotion,
    save_disposition,
    view_visible_to,
)
from mentorapp.observability import get_logger
from mentorapp.storage import DataSource, Grid, GridDeepLink, GridView

log = get_logger(__name__)


class ViewNotFoundError(LookupError):
    """No live ``gridView`` row carries the id; maps to a 404 envelope."""

    def __init__(self, grid_view_id: uuid.UUID) -> None:
        self.grid_view_id = grid_view_id
        super().__init__(f"no grid view {grid_view_id}")


class DeepLinkNotFoundError(LookupError):
    """No live ``gridDeepLink`` (or its grid) carries the key; maps to a 404 envelope."""

    def __init__(self, deep_link_key: str) -> None:
        self.deep_link_key = deep_link_key
        super().__init__(f"no deep link {deep_link_key!r}")


def view_facts(view: GridView) -> ViewFacts:
    """The stored row in the decision vocabulary — the one storage→rule adapter.

    ``userID`` null IS the system-view fact (WTK-041's owner association), so
    no separate ``viewType`` check can drift from it here.
    """
    return ViewFacts(
        view_id=view.grid_view_id,
        owner_id=view.user_id,
        read_only=view.read_only_flag,
        temporary_modified=view.temporary_modified_flag,
    )


def load_live_view(session: Session, grid_view_id: uuid.UUID) -> GridView:
    """The live ``gridView`` row, or :class:`ViewNotFoundError` — a deleted
    view and an unknown id answer identically, so nothing can be probed."""
    view = session.scalars(
        select(GridView).where(
            GridView.grid_view_id == grid_view_id,
            GridView.deleted_at.is_(None),
        )
    ).one_or_none()
    if view is None:
        raise ViewNotFoundError(grid_view_id)
    return view


def stored_save_disposition(
    session: Session, *, grid_view_id: uuid.UUID, user_id: uuid.UUID
) -> str:
    """Where saving this stored view's modifications may land for this user
    (REQ-017): the editor asks this to offer save-in-place vs save-as-user."""
    return save_disposition(view_facts(load_live_view(session, grid_view_id)), user_id=user_id)


def authorize_stored_view_management(
    session: Session, *, grid_view_id: uuid.UUID, user_id: uuid.UUID, action: str
) -> GridView:
    """Load-and-authorize for update/rename/delete (REQ-017), in one call.

    System views are read-only for everyone; users manage exactly their own
    saved views. Raises :class:`ViewNotFoundError` for a dead or unknown id,
    :class:`~mentorapp.access.views.ViewPermissionError` when the view is not
    this user's to change.
    """
    view = load_live_view(session, grid_view_id)
    authorize_view_management(view_facts(view), user_id=user_id, action=action)
    return view


def authorize_stored_view_promotion(
    session: Session, *, grid_view_id: uuid.UUID, user_id: uuid.UUID
) -> GridView:
    """Load-and-authorize promotion to a system view — the one sharing path
    (REQ-017), admin-gated by the stored ``gridView.promote`` capability."""
    view = load_live_view(session, grid_view_id)
    authorize_view_promotion(
        view_facts(view), lookup=StoredCapabilityRegistry(session), user_id=user_id
    )
    return view


def authorize_stored_data_source_authoring(session: Session, *, user_id: uuid.UUID) -> None:
    """REQ-019's stored gate: only a persisted ``adminSql.author`` grant
    admits the caller to source authoring; running stays REQ-006's boundary."""
    authorize_data_source_authoring(StoredCapabilityRegistry(session), user_id=user_id)


# --- Deep-link fact assembly (REQ-028) ---------------------------------------------


@dataclass(frozen=True)
class NamedViewLinkFacts:
    """A link that names a view: everything the pure resolver consumes.

    ``has_data_source_access`` is the REQ-006 fact for the NAMED view's
    source — decided here, once, through the stored grant boundary, so the
    endpoint has no access question left to answer on its own.
    """

    grid_key: str
    view_id: uuid.UUID
    view_owner_id: uuid.UUID | None
    has_data_source_access: bool


@dataclass(frozen=True)
class GridOnlyLinkFacts:
    """A link that names only the grid: it opens the recipient's last-used
    view (WTK-042 semantics), and THAT view's own read path re-authorizes —
    there is no named source to pre-check."""

    grid_key: str


def load_deep_link_facts(
    session: Session, *, deep_link_key: str, user_roles: frozenset[str]
) -> NamedViewLinkFacts | GridOnlyLinkFacts:
    """One lookup from a shared link key to the resolver's input (REQ-028).

    A dead link — unknown key, retired link row, or retired grid — raises
    :class:`DeepLinkNotFoundError`; all three answer identically. A link
    whose NAMED VIEW has since been deleted degrades to grid-only facts (the
    grid still exists; the recipient lands on their last-used view) rather
    than opening a dead view or leaking why. A retired data source yields
    ``has_data_source_access`` ``False`` — a source nobody can run is closed,
    not open, to link recipients (deny by default, REQ-006).
    """
    row = session.execute(
        select(GridDeepLink, Grid.grid_key)
        .join(Grid, GridDeepLink.grid_id == Grid.grid_id)
        .where(
            GridDeepLink.deep_link_key == deep_link_key,
            GridDeepLink.deleted_at.is_(None),
            Grid.deleted_at.is_(None),
        )
    ).one_or_none()
    if row is None:
        raise DeepLinkNotFoundError(deep_link_key)
    link, grid_key = row
    if link.grid_view_id is None:
        return GridOnlyLinkFacts(grid_key)
    try:
        view = load_live_view(session, link.grid_view_id)
    except ViewNotFoundError:
        log.info(
            "deep link names a retired view; degrading to grid-only",
            extra={"context": {"deepLinkKey": deep_link_key, "viewID": str(link.grid_view_id)}},
        )
        return GridOnlyLinkFacts(grid_key)
    source_key = session.scalars(
        select(DataSource.data_source_key).where(
            DataSource.data_source_id == view.data_source_id,
            DataSource.deleted_at.is_(None),
        )
    ).one_or_none()
    has_access = source_key is not None and roles_cover_data_source(
        StoredGrantRegistry(session), data_source_key=source_key, user_roles=user_roles
    )
    return NamedViewLinkFacts(
        grid_key=grid_key,
        view_id=view.grid_view_id,
        view_owner_id=view.user_id,
        has_data_source_access=has_access,
    )


def stored_view_visible_to(
    session: Session, *, grid_view_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """The stored form of the visibility fact (REQ-028): quiet, for deriving
    what to list — the view selector shows system views plus the user's own."""
    return view_visible_to(view_facts(load_live_view(session, grid_view_id)), user_id)
