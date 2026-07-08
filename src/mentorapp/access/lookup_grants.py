"""LookupDataAccess: the grant model for relationship type-ahead search (WTK-061).

REQ-036's lookup control searches "the related data set under its own
permissions". This module fixes what that means at the layer that decides it:

- **Lookup permission IS data-source permission.** The type-ahead over a
  related entity is governed by the grant on that entity's *bound* data
  source — the same REQ-006 boundary that gates the entity's grids and
  areas, re-used, never a second lookup-specific grant table that could
  drift from it. Granting or revoking a role on the source changes the
  rail, the grids, AND every lookup over that entity together, on the next
  keystroke.
- **The binding is configuration, not permission.** Grants name data-source
  keys; nothing persisted names "the source that governs entity X", so the
  binding (:class:`LookupBinding`) is the one new fact this design adds.
  It rides the :class:`LookupSourceResolver` seam with an in-memory
  reference, the same shape grants took before WTK-007 — the persisted
  binding is a storage-area entity design, not decided here.
- **Unbound means closed.** An entity nobody bound a source to has an
  ungoverned lookup — a configuration bug, not an open door (the grants
  stance), so the quiet form answers False and the attempt form raises
  :class:`LookupUnboundError`, loudly distinct from a role denial. This is
  deliberately the OPPOSITE of the areas rule, where a ``None`` source
  means open-to-everyone (Home): a lookup always reads real records, so
  there is no source-less-open case.
- **Two decision forms, mirroring areas.** :func:`is_lookup_searchable` is
  the quiet form the control's render takes as ``has_access`` — a miss
  keeps the field visible and typing gets the no-access explainer
  (:func:`~mentorapp.ui.lookup_control.resolve_suggestions`), never a
  hidden or grayed field. :func:`authorize_lookup_search` is the attempt
  form for the suggestion request itself, riding
  :func:`~mentorapp.access.grants.authorize_data_source` so the denial log
  and typed error stay the one boundary's.
- **Row scoping travels with the grant decision.** A user-scoped source
  (non-null ``userRowFilter``) must scope suggestions exactly as it scopes
  the grid — a lookup may never widen what its governing source shows.
  :func:`stored_lookup_scope` is the endpoint's one entry point: it
  authorizes and returns the :class:`LookupScope` in the same call, so a
  suggestion query cannot pass the grant check and forget the row filter.
  The query itself (``trigram_search_filter`` over the related entity's
  searchable columns, live rows only) is the API layer's to compose —
  access (rank 2) cannot import it; this module hands the endpoint every
  access fact the deferred WTK-060 wiring needs.

The related entity type arrives as an argument: deriving it from the
entity-named field key (``mentorID`` → ``mentor``) already has a canonical
home in :func:`~mentorapp.ui.lookup_control.related_entity_type`, which the
endpoint applies before asking access to decide.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.access.grants import (
    GrantLookup,
    StoredGrantRegistry,
    authorize_data_source,
    roles_cover_data_source,
)
from mentorapp.observability import get_logger
from mentorapp.storage import DataSource, LookupSourceBinding

log = get_logger(__name__)


class LookupUnboundError(LookupError):
    """No data source is bound to govern the related entity's lookup.

    A configuration bug, deliberately not :class:`~mentorapp.access.grants.
    DataSourceAccessError`: a role denial is an expected, per-user fact the
    control explains in place, while an unbound entity means the lookup was
    shipped ungoverned and an administrator must bind a source. The API maps
    both to the control's never-hide no-access state, but this one logs as
    the defect it is.
    """

    def __init__(self, related_entity_type: str) -> None:
        self.related_entity_type = related_entity_type
        super().__init__(f"no lookup source bound for entity {related_entity_type!r}")


@dataclass(frozen=True)
class LookupBinding:
    """One configuration fact: this source's grants govern this entity's lookup."""

    related_entity_type: str
    data_source_key: str


class LookupSourceResolver(Protocol):
    """The configuration seam: which source governs an entity's lookup.

    Persistence of bindings is a storage-area entity design (the WTK-001
    precedent grants followed); every decision here re-resolves, so a
    re-bound entity changes the very next keystroke's answer.
    """

    def lookup_source_key(self, related_entity_type: str) -> str | None:
        """Return the bound source key, or ``None`` when the entity is unbound."""
        ...


class InMemoryLookupSources:
    """Reference :class:`LookupSourceResolver` for tests and pre-persistence wiring."""

    def __init__(self, bindings: list[LookupBinding] | None = None) -> None:
        self._by_entity: dict[str, str] = {
            b.related_entity_type: b.data_source_key for b in (bindings or [])
        }

    def bind(self, binding: LookupBinding) -> None:
        self._by_entity[binding.related_entity_type] = binding.data_source_key

    def lookup_source_key(self, related_entity_type: str) -> str | None:
        return self._by_entity.get(related_entity_type)


class StoredLookupSources:
    """The production :class:`LookupSourceResolver`: the persisted binding table.

    Reads the one live :class:`~mentorapp.storage.LookupSourceBinding` per
    entity, so a re-bind (a new live row, the prior soft-deleted) reaches the
    next keystroke with no restart — the seam's re-resolve-every-time contract
    over durable state instead of the in-memory reference. An unbound entity
    is a missing row, answered ``None`` exactly as the reference resolver
    does, so every caller's unbound handling is unchanged.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def lookup_source_key(self, related_entity_type: str) -> str | None:
        return self._session.scalars(
            select(LookupSourceBinding.data_source_key).where(
                LookupSourceBinding.related_entity_type == related_entity_type,
                LookupSourceBinding.deleted_at.is_(None),
            )
        ).one_or_none()


def is_lookup_searchable(
    resolver: LookupSourceResolver,
    grants: GrantLookup,
    *,
    related_entity_type: str,
    user_roles: frozenset[str],
) -> bool:
    """The quiet form: the control's ``has_access`` input on render.

    A miss never hides the field (REQ-036's control stays visible and
    explains); it only selects the no-access suggestion phase. Unbound
    entities answer False too — closed is the only safe rendering — and are
    logged, because unlike a role miss they are configuration-relevant.
    """
    source_key = resolver.lookup_source_key(related_entity_type)
    if source_key is None:
        log.info(
            "lookup source unbound",
            extra={"context": {"relatedEntityType": related_entity_type}},
        )
        return False
    return roles_cover_data_source(grants, data_source_key=source_key, user_roles=user_roles)


def authorize_lookup_search(
    resolver: LookupSourceResolver,
    grants: GrantLookup,
    *,
    related_entity_type: str,
    user_id: uuid.UUID,
    user_roles: frozenset[str],
) -> str:
    """The attempt form: gate one suggestion request; returns the governing key.

    Raises :class:`LookupUnboundError` for an ungoverned entity and the
    boundary's own :class:`~mentorapp.access.grants.DataSourceAccessError`
    (denial-logged there — one boundary, one audit trail) for a role miss.
    The returned key is the one the decision actually used, so the caller
    scopes exactly what was checked.
    """
    source_key = resolver.lookup_source_key(related_entity_type)
    if source_key is None:
        log.error(
            "lookup search attempted on unbound entity",
            extra={
                "context": {
                    "relatedEntityType": related_entity_type,
                    "userID": str(user_id),
                }
            },
        )
        raise LookupUnboundError(related_entity_type)
    authorize_data_source(
        grants, data_source_key=source_key, user_id=user_id, user_roles=user_roles
    )
    return source_key


@dataclass(frozen=True)
class LookupScope:
    """Every access fact one authorized suggestion query runs under.

    ``user_row_filter`` non-null means the governing source is user-scoped:
    the endpoint must filter the related entity's rows to the session user
    on that column, exactly as ``execute_admin_sql`` binds
    ``:currentUserID`` for the source itself — the suggestion set is the
    grid's set, never wider. The trigram search and live-rows predicate are
    the API layer's to add; they are search shape, not permission.
    """

    related_entity_type: str
    data_source_key: str
    user_row_filter: str | None


def stored_lookup_scope(
    session: Session,
    resolver: LookupSourceResolver,
    *,
    related_entity_type: str,
    user_id: uuid.UUID,
    user_roles: frozenset[str],
) -> LookupScope:
    """Authorize a suggestion request against the persisted grants — the one entry point.

    Authorization and scope come back from the same call so an endpoint
    cannot pass the grant check and drop the row filter. The grant registry
    joins through live sources only, so a passing check guarantees the
    scope row exists; a binding to a retired source denies exactly like an
    ungranted one (no probing which keys are live), which is why the row
    load may come second.
    """
    source_key = authorize_lookup_search(
        resolver,
        StoredGrantRegistry(session),
        related_entity_type=related_entity_type,
        user_id=user_id,
        user_roles=user_roles,
    )
    source = session.scalars(
        select(DataSource).where(
            DataSource.data_source_key == source_key,
            DataSource.deleted_at.is_(None),
        )
    ).one()
    return LookupScope(
        related_entity_type=related_entity_type,
        data_source_key=source_key,
        user_row_filter=source.user_row_filter,
    )
