"""The identity bridge: where a CRM identity becomes an app identity.

The design has exactly two verified-identity shapes, and this module is the
one seam between them. :class:`~mentorapp.crm.auth.CrmVerifiedIdentity` is
what the CRM proved (its own user id, its role/team names, the CRM-issued
credential); :class:`VerifiedIdentity` is what the rest of the app operates
on (the ``appUser`` key, the grant-vocabulary roles, the credential carried
through to session custody). Everything between login and session
establishment funnels through :class:`IdentityBridge` — there is no second
place where the mapping could be re-decided or drift (this seam replaced two
same-named dataclasses that had grown in parallel).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.crm.auth import CrmUserCredential, CrmVerifiedIdentity
from mentorapp.observability import get_logger
from mentorapp.storage import AppUser, uuid7

log = get_logger(__name__)


@dataclass(frozen=True)
class VerifiedIdentity:
    """The one canonical app-side verified identity.

    ``user_id`` is the ``appUser`` key the bridge found or provisioned;
    ``role_names`` are the app's grant-vocabulary roles mapped from the CRM's
    role/team names — session-scoped, never persisted per user, because the
    CRM is the role source. ``crm_credential`` is the CRM-issued act-as-user
    token from the same verification exchange, carried through so the session
    layer can take custody of it for :class:`~mentorapp.crm.auth.CrmAccess`.
    """

    user_id: uuid.UUID
    role_names: frozenset[str]
    crm_credential: CrmUserCredential


class IdentityBridge(Protocol):
    """The single seam where a CRM identity becomes an app identity.

    ``resolve`` finds-or-provisions the ``appUser`` row by ``crmUserID``
    (``storage/auth.py``), maps the CRM's role names onto the app's grant
    role vocabulary, and carries the CRM credential through to session
    establishment. Nothing else may build a :class:`VerifiedIdentity` from
    CRM material — one seam is what keeps the two identity shapes from ever
    diverging again.
    """

    def resolve(self, identity: CrmVerifiedIdentity) -> VerifiedIdentity: ...


@dataclass
class InMemoryIdentityBridge:
    """Reference :class:`IdentityBridge` for tests and pre-persistence wiring.

    ``known`` maps a CRM user id to the (app ``user_id``, mapped roles) pair a
    real bridge would read from ``appUser`` and the role-vocabulary mapping.
    Unknown CRM users are provisioned on first resolve — a fresh app id, with
    the CRM role names passed through unmapped — mirroring the find-or-
    provision contract without a database.
    """

    known: dict[str, tuple[uuid.UUID, frozenset[str]]] = field(default_factory=dict)

    def resolve(self, identity: CrmVerifiedIdentity) -> VerifiedIdentity:
        if identity.crm_user_id not in self.known:
            self.known[identity.crm_user_id] = (uuid7(), identity.role_names)
        user_id, role_names = self.known[identity.crm_user_id]
        return VerifiedIdentity(
            user_id=user_id,
            role_names=role_names,
            crm_credential=identity.credential,
        )


class StoredIdentityBridge:
    """:class:`IdentityBridge` over ``appUser``: find-or-provision by ``crmUserID``.

    Role mapping is deliberately pass-through: the grant vocabulary IS the
    CRM's staff-role/team names (``dataSourceRoleGrant.roleName`` is matched
    against the session's CRM-captured roles), so there is no translation
    table to consult and the CRM stays the one role source. Provisioning
    flushes but does not commit — the new row lands with the session save
    that establishes the login, never as an orphan of a failed one.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(self, identity: CrmVerifiedIdentity) -> VerifiedIdentity:
        row = self._session.scalars(
            select(AppUser).where(
                AppUser.crm_user_id == identity.crm_user_id,
                AppUser.deleted_at.is_(None),
            )
        ).one_or_none()
        if row is None:
            row = AppUser(crm_user_id=identity.crm_user_id, username=identity.username)
            self._session.add(row)
            self._session.flush()
            log.info(
                "app user provisioned from CRM identity",
                extra={"context": {"userID": str(row.user_id)}},
            )
        return VerifiedIdentity(
            user_id=row.user_id,
            role_names=identity.role_names,
            crm_credential=identity.credential,
        )
