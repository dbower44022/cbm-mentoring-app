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

from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.observability import get_logger
from mentorapp.storage import (
    AdminSqlSource,
    DataSource,
    DataSourceRoleGrant,
    execute_admin_sql,
    utcnow,
)

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


def roles_cover_data_source(
    lookup: GrantLookup, *, data_source_key: str, user_roles: frozenset[str]
) -> bool:
    """Whether any of the user's roles is granted on the source.

    The one grant decision, in its quiet form: deriving what to SHOW (the
    Areas rail, WTK-025) asks this once per area on every render, where a
    miss means "not yours to see" — not the audit-relevant denial an actual
    open attempt is. :func:`authorize_data_source` wraps this same decision
    with the denial log and typed error for the attempt path.
    """
    return bool(user_roles & lookup.roles_granted(data_source_key))


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
    if roles_cover_data_source(lookup, data_source_key=data_source_key, user_roles=user_roles):
        return
    granted = lookup.roles_granted(data_source_key)
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


class DataSourceNotFoundError(LookupError):
    """No live ``dataSource`` row carries the key; maps to a 404 envelope."""

    def __init__(self, data_source_key: str) -> None:
        self.data_source_key = data_source_key
        super().__init__(f"no data source {data_source_key!r}")


class StoredGrantRegistry:
    """:class:`GrantLookup` over the persisted ``dataSourceRoleGrant`` rows.

    Reads live rows only, joined through a live ``dataSource``: revoking a
    grant (soft delete) or retiring the source itself makes ``roles_granted``
    stop naming the role on the very next check — revocation needs no sweep
    of dependents because every dependent surface (panels, views, exports)
    reaches the data through :func:`run_stored_data_source` and is re-checked
    on every run.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def roles_granted(self, data_source_key: str) -> frozenset[str]:
        rows = self._session.scalars(
            select(DataSourceRoleGrant.role_name)
            .join(DataSource, DataSourceRoleGrant.data_source_id == DataSource.data_source_id)
            .where(
                DataSource.data_source_key == data_source_key,
                DataSource.deleted_at.is_(None),
                DataSourceRoleGrant.deleted_at.is_(None),
            )
        )
        return frozenset(rows)


def _live_source(session: Session, data_source_key: str) -> DataSource | None:
    return session.scalars(
        select(DataSource).where(
            DataSource.data_source_key == data_source_key,
            DataSource.deleted_at.is_(None),
        )
    ).one_or_none()


def _live_grant(
    session: Session, data_source_key: str, role_name: str
) -> DataSourceRoleGrant | None:
    return session.scalars(
        select(DataSourceRoleGrant)
        .join(DataSource, DataSourceRoleGrant.data_source_id == DataSource.data_source_id)
        .where(
            DataSource.data_source_key == data_source_key,
            DataSource.deleted_at.is_(None),
            DataSourceRoleGrant.role_name == role_name,
            DataSourceRoleGrant.deleted_at.is_(None),
        )
    ).one_or_none()


def grant_data_source_role(
    session: Session,
    *,
    data_source_key: str,
    role_name: str,
    granted_by: uuid.UUID | None = None,
) -> DataSourceRoleGrant:
    """Approve ``role_name`` to run the source; idempotent while the grant is live.

    Raises :class:`DataSourceNotFoundError` when no live source carries the
    key — a grant on nothing is an admin mistake, not a future approval.
    Re-granting after a revocation inserts a fresh row (the unique index is
    live-rows-only), so the revocation stays in history.
    """
    source = _live_source(session, data_source_key)
    if source is None:
        raise DataSourceNotFoundError(data_source_key)
    existing = _live_grant(session, data_source_key, role_name)
    if existing is not None:
        return existing
    grant = DataSourceRoleGrant(
        data_source_id=source.data_source_id,
        role_name=role_name,
        created_by=granted_by,
        modified_by=granted_by,
    )
    session.add(grant)
    session.flush()
    log.info(
        "data source role granted",
        extra={"context": {"dataSourceKey": data_source_key, "roleName": role_name}},
    )
    return grant


def revoke_data_source_role(
    session: Session,
    *,
    data_source_key: str,
    role_name: str,
    revoked_by: uuid.UUID | None = None,
) -> bool:
    """Withdraw the approval; returns whether a live grant was revoked.

    Soft delete (DB-S3): the row survives as grant history and the partial
    unique index frees the (source, role) pair for a later re-grant. Every
    dependent read re-authorizes through :class:`StoredGrantRegistry` on its
    next run, so no dependent cleanup is needed — or possible to forget.
    """
    grant = _live_grant(session, data_source_key, role_name)
    if grant is None:
        return False
    grant.deleted_at = utcnow()
    grant.deleted_by = revoked_by
    grant.modified_by = revoked_by
    session.flush()
    log.info(
        "data source role revoked",
        extra={"context": {"dataSourceKey": data_source_key, "roleName": role_name}},
    )
    return True


def load_stored_source(session: Session, data_source_key: str) -> AdminSqlSource:
    """The stored ``dataSource`` row in the executor's form.

    A non-null ``userRowFilter`` on the row IS the user-scoped declaration:
    the flag travels with the stored source, so the executor's server-side
    ``:currentUserID`` binding cannot be dropped by a caller re-describing
    the source. Raises :class:`DataSourceNotFoundError` when no live row
    carries the key.
    """
    source = _live_source(session, data_source_key)
    if source is None:
        raise DataSourceNotFoundError(data_source_key)
    return AdminSqlSource(
        data_source_key=source.data_source_key,
        sql_text=source.data_source_sql,
        user_scoped_flag=source.user_row_filter is not None,
    )


def run_stored_data_source(
    session: Session,
    data_source_key: str,
    *,
    user_id: uuid.UUID,
    user_roles: frozenset[str],
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run a persisted data source as a user — the API-facing entry point.

    Authorization runs before the source is even loaded: an unknown key and
    an ungranted key both raise :class:`DataSourceAccessError`, so a denied
    caller cannot probe which source keys exist. A grant can only pass for a
    live source (the registry joins through it), which is what lets the load
    come second.
    """
    authorize_data_source(
        StoredGrantRegistry(session),
        data_source_key=data_source_key,
        user_id=user_id,
        user_roles=user_roles,
    )
    source = load_stored_source(session, data_source_key)
    return execute_admin_sql(session, source, current_user_id=user_id, params=params)
