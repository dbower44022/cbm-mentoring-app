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

from mentorapp.crm.auth import CrmUserCredential, CrmVerifiedIdentity
from mentorapp.storage import uuid7


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
