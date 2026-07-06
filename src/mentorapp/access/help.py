"""HelpAdministration: the admin gate on help configuration (WTK-102, REQ-043).

The help system's permission model has exactly one decision, and it is not
about reading: RESOLVING help (page → URL) is every signed-in user's read —
Help is never hidden and never a dead control (REQ-043), so no capability
gates the resolve endpoint. CONFIGURING the mapping table and the fallback
settings is the Administrator persona's act, gated by the persisted
``help.admin`` capability through the ONE capability boundary
(:func:`~mentorapp.access.views.authorize_capability` over ``accessGrant``
rows) — the ``workprocess.register`` pattern: configuring and using are
different permissions with different homes, on purpose.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from mentorapp.access.views import (
    CAP_HELP_ADMIN,
    CapabilityLookup,
    StoredCapabilityRegistry,
    authorize_capability,
)


def authorize_help_administration(lookup: CapabilityLookup, *, user_id: uuid.UUID) -> None:
    """REQ-043's admin gate, by name: only ``help.admin`` holders configure
    the page → URL mappings and the help settings. Configuring only —
    resolving help stays every user's read, never a capability."""
    authorize_capability(lookup, user_id=user_id, capability=CAP_HELP_ADMIN)


def authorize_stored_help_administration(session: Session, *, user_id: uuid.UUID) -> None:
    """The stored form of the admin gate — the ``accessGrant`` rows decide
    (the :func:`~mentorapp.access.workprocess.authorize_stored_workprocess_registration`
    shape), so revoking the capability changes the very next attempt."""
    authorize_help_administration(StoredCapabilityRegistry(session), user_id=user_id)
