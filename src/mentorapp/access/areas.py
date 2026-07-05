"""UserAreaAccess: which Areas a user may enter, derived — never assigned (WTK-025).

REQ-003's Areas rail and REQ-015's startup/deep-link fallbacks both consume
one answer: the set of panels this user may open. The UI layer already fixed
the rule ("panel permission IS data-source permission", :mod:`~mentorapp.ui.
navigation`) and the fallbacks themselves (``resolve_startup_target``,
``resolve_startup_panel`` — land on Home with the educate-voice explanation,
never a blank screen). What was missing is the derivation those surfaces take
as ``accessible_panel_keys`` input; this module is it.

The requirement's ``user_area_access`` is deliberately realized as THIS
derivation over the REQ-006 grant boundary, not a new assignment table: a
per-user area table would be the second permission model the navigation
design forbids, able to drift from the grants ``resolve_pin`` re-checks on
every open. Granting or revoking a data-source role therefore changes the
rail, panel permissioning, and the fallbacks together, on the next render —
one act, one boundary, no sweep of dependents.

Layering: access (rank 2) cannot see the UI's ``Panel``, so the area is
described here by the same two facts permission needs — its key and its
optional data source — and the shell maps its panels onto
:class:`AreaDescriptor` when it asks.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from mentorapp.access.grants import (
    GrantLookup,
    authorize_data_source,
    roles_cover_data_source,
)
from mentorapp.observability import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class AreaDescriptor:
    """An Area as the access decision sees it: a key and its permission source.

    ``data_source_key`` of ``None`` means the area carries no data source of
    its own (Home) and is open to every signed-in user — the same rule the
    UI's ``Panel`` states, expressed at the layer that decides it.
    """

    area_key: str
    data_source_key: str | None = None


def is_area_accessible(
    area: AreaDescriptor, *, grants: GrantLookup, user_roles: frozenset[str]
) -> bool:
    """The visibility form of the decision: quiet, for deriving what to show."""
    if area.data_source_key is None:
        return True
    return roles_cover_data_source(
        grants, data_source_key=area.data_source_key, user_roles=user_roles
    )


def authorize_area(
    area: AreaDescriptor,
    *,
    grants: GrantLookup,
    user_id: uuid.UUID,
    user_roles: frozenset[str],
) -> None:
    """The attempt form: opening an area a user was not shown is audit-relevant.

    A source-less area (Home) always passes — Home is the fallback target,
    so it can never itself be inaccessible. Otherwise raises the REQ-006
    :class:`~mentorapp.access.grants.DataSourceAccessError`, which the API
    maps to a 403 envelope and the navigation fallback explains.
    """
    if area.data_source_key is None:
        return
    authorize_data_source(
        grants,
        data_source_key=area.data_source_key,
        user_id=user_id,
        user_roles=user_roles,
    )


def accessible_area_keys(
    areas: Sequence[AreaDescriptor],
    *,
    grants: GrantLookup,
    user_id: uuid.UUID,
    user_roles: frozenset[str],
) -> tuple[str, ...]:
    """user_area_access: the area keys this user may enter, in the caller's order.

    This is the ``accessible_panel_keys`` input to ``HomeFrame.areas_for``
    (REQ-003) and ``resolve_startup_panel`` (REQ-015). Order is preserved
    because the rail's order is the shell's decision (system default first),
    not permission's. May be empty of everything but Home — the rail then
    renders nothing, and every startup choice still resolves to Home.
    """
    accessible = tuple(
        area.area_key
        for area in areas
        if is_area_accessible(area, grants=grants, user_roles=user_roles)
    )
    log.info(
        "user area access derived",
        extra={
            "context": {
                "userID": str(user_id),
                "areaCount": len(areas),
                "accessibleCount": len(accessible),
            }
        },
    )
    return accessible
