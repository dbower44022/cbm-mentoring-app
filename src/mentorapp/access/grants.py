"""DataSourceAccessControl: per-source grants + the injected row filter (REQ-006).

The grant is the approval boundary: a user can run only data sources whose
grant list names one of their staff roles, and a source with no grants is
closed to everyone — deny by default, an ungoverned source is a bug, not an
open door. Row-level scoping is NOT re-decided here: user-scoped sources get
the session user bound server-side by ``execute_admin_sql``, which already
rejects any caller-supplied ``currentUserID``. This module adds the missing
half the executor deliberately left outside itself — who may run a source at
all — and composes the two so the API has one entry point where neither check
can be skipped.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.orm import Session

from mentorapp.observability import get_logger
from mentorapp.storage import AdminSqlSource, execute_admin_sql

log = get_logger(__name__)


class DataSourceAccessError(Exception):
    """The user holds no role granted on the data source; maps to a 403 envelope."""

    def __init__(self, data_source_key: str, user_id: uuid.UUID) -> None:
        self.data_source_key = data_source_key
        self.user_id = user_id
        super().__init__(f"no grant on data source {data_source_key!r}")


@dataclass(frozen=True)
class SourceGrant:
    """One approval: the named staff role may run the named data source."""

    data_source_key: str
    role_name: str


class GrantLookup(Protocol):
    """The persistence seam for grants (entity design: WTK-001, storage area)."""

    def roles_granted(self, data_source_key: str) -> frozenset[str]:
        """Return the role names granted on the source; empty when none."""
        ...


class InMemoryGrantRegistry:
    """Reference :class:`GrantLookup` for tests and pre-persistence wiring."""

    def __init__(self, grants: list[SourceGrant] | None = None) -> None:
        self._grants: set[SourceGrant] = set(grants or [])

    def add(self, grant: SourceGrant) -> None:
        self._grants.add(grant)

    def roles_granted(self, data_source_key: str) -> frozenset[str]:
        return frozenset(
            g.role_name for g in self._grants if g.data_source_key == data_source_key
        )


def authorize_data_source(
    lookup: GrantLookup,
    *,
    data_source_key: str,
    user_id: uuid.UUID,
    user_roles: frozenset[str],
) -> None:
    """Raise :class:`DataSourceAccessError` unless a role grant covers the user.

    Denials are logged with the source and user — the grant list is the
    security boundary, so refusals are an audit-relevant signal, not noise.
    """
    granted = lookup.roles_granted(data_source_key)
    if user_roles & granted:
        return
    log.info(
        "data source access denied",
        extra={
            "context": {
                "dataSourceKey": data_source_key,
                "userID": str(user_id),
                "grantedRoleCount": len(granted),
            }
        },
    )
    raise DataSourceAccessError(data_source_key, user_id)


def run_data_source(
    session: Session,
    source: AdminSqlSource,
    *,
    lookup: GrantLookup,
    user_id: uuid.UUID,
    user_roles: frozenset[str],
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The one entry point for running a data source as a user.

    Grant check first, then the executor — which validates the SQL and, for
    user-scoped sources, binds this ``user_id`` as ``:currentUserID``
    server-side. The user identity comes from the resolved session record,
    never from the request body, so the row filter cannot be bypassed by
    naming a different user.
    """
    authorize_data_source(
        lookup,
        data_source_key=source.data_source_key,
        user_id=user_id,
        user_roles=user_roles,
    )
    return execute_admin_sql(session, source, current_user_id=user_id, params=params)
