"""WorkprocessAccess: inherited visibility + the admin registration gate (WTK-091).

REQ-041 fixes the permission model in one sentence: a workprocess is
visible and launchable for exactly the users who can access its target data
sources — no separate per-app grants. This module realizes that as a
DERIVATION over the REQ-006 boundary (:mod:`mentorapp.access.grants`), the
same shape as :mod:`mentorapp.access.areas`: a second workprocess-permission
table could drift from the grants every launch re-checks, so none exists.
Granting or revoking a data-source role changes the action list and
launchability together, on the next read — one act, one boundary.

Two decisions, in their two standard forms:

- **Visibility (quiet).** :func:`visible_workprocesses` derives one data
  source's action-list entries for one user — the registrations targeting
  the source, served whole when the user's roles cover the source and empty
  otherwise. Within a covered source the list is never trimmed per
  registration (the never-hide rule): restriction happens by targeting a
  TIGHTER data source, not by hiding actions.
- **Launch (attempt).** :func:`authorize_workprocess_launch` re-checks the
  same boundary at launch time and raises the REQ-006
  :class:`~mentorapp.access.grants.DataSourceAccessError` on a miss —
  audit-relevant, exactly like opening an area. It also refuses a launch
  from a source the registration does not target
  (:class:`WorkprocessNotTargetedError`, the 404-shaped answer): an
  untargeted source is not an action list the workprocess is on, so the
  combination must not be probeable into existence.

Registering is different in kind: it is the Administrator persona's act
(REQ-041 "an administrator registers…"), gated by the persisted
``workprocess.register`` capability through the ONE capability boundary
(:func:`~mentorapp.access.views.authorize_capability` over ``accessGrant``
rows) — the ``adminSql.author`` pattern: authoring and using are different
permissions with different homes, on purpose.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from mentorapp.access.grants import (
    GrantLookup,
    StoredGrantRegistry,
    authorize_data_source,
    roles_cover_data_source,
)
from mentorapp.access.views import (
    CAP_WORKPROCESS_REGISTER,
    CapabilityLookup,
    StoredCapabilityRegistry,
    authorize_capability,
)
from mentorapp.observability import get_logger
from mentorapp.storage import WorkprocessRegistration, registrations_for_data_source

log = get_logger(__name__)


class WorkprocessNotTargetedError(LookupError):
    """The registration does not target the launching source; maps to a 404 envelope.

    404-shaped on purpose: for this data source's action list the
    workprocess does not exist, and answering anything richer would let the
    (registration, source) pairing be probed across sources the caller was
    never shown.
    """

    def __init__(self, workprocess_name: str, data_source_key: str) -> None:
        self.workprocess_name = workprocess_name
        self.data_source_key = data_source_key
        super().__init__(f"workprocess {workprocess_name!r} not on {data_source_key!r}")


def visible_workprocesses(
    session: Session,
    *,
    data_source_key: str,
    user_roles: frozenset[str],
    grants: GrantLookup | None = None,
) -> list[WorkprocessRegistration]:
    """One source's action-list entries for one user (REQ-041) — the quiet form.

    The inheritance rule verbatim: covered source → every live registration
    targeting it (all-or-nothing, never trimmed per registration); uncovered
    source → empty, because the user does not see that action list at all.
    ``grants`` defaults to the stored REQ-006 registry; tests may pass an
    in-memory lookup — either way it is the ONE grant boundary, never a
    re-implementation.
    """
    lookup = grants if grants is not None else StoredGrantRegistry(session)
    if not roles_cover_data_source(
        lookup, data_source_key=data_source_key, user_roles=user_roles
    ):
        return []
    return registrations_for_data_source(session, data_source_key)


def authorize_workprocess_launch(
    session: Session,
    registration: WorkprocessRegistration,
    *,
    data_source_key: str,
    user_id: uuid.UUID,
    user_roles: frozenset[str],
    grants: GrantLookup | None = None,
) -> None:
    """The attempt form: may THIS user launch THIS workprocess from THIS source?

    Two gates. The registration must target the launching source
    (:class:`WorkprocessNotTargetedError` otherwise — checked first, so a
    denied caller learns nothing about sources the workprocess is not on),
    and the user's roles must cover the source — the inherited REQ-006
    decision, raised and logged by
    :func:`~mentorapp.access.grants.authorize_data_source` exactly like
    every other data-source denial. Selection-contract fit is deliberately
    NOT decided here: a wrong selection is a mistake to explain (the
    educate-voice API refusal), not an access denial to audit.
    """
    targeted = {
        link.data_source.data_source_key
        for link in registration.data_source_links
        if link.deleted_at is None and link.data_source.deleted_at is None
    }
    if data_source_key not in targeted:
        log.info(
            "workprocess launch refused: source not targeted",
            extra={
                "context": {
                    "workprocessRegistrationID": str(registration.workprocess_registration_id),
                    "dataSourceKey": data_source_key,
                    "userID": str(user_id),
                }
            },
        )
        raise WorkprocessNotTargetedError(registration.workprocess_name, data_source_key)
    authorize_data_source(
        grants if grants is not None else StoredGrantRegistry(session),
        data_source_key=data_source_key,
        user_id=user_id,
        user_roles=user_roles,
    )


def authorize_workprocess_registration(lookup: CapabilityLookup, *, user_id: uuid.UUID) -> None:
    """REQ-041's admin gate, by name: only ``workprocess.register`` holders
    manage registrations. Registering only — seeing and launching stay the
    inherited data-source boundary above, never a capability."""
    authorize_capability(lookup, user_id=user_id, capability=CAP_WORKPROCESS_REGISTER)


def authorize_stored_workprocess_registration(session: Session, *, user_id: uuid.UUID) -> None:
    """The stored form of the admin gate — the ``accessGrant`` rows decide
    (the :func:`~mentorapp.access.view_enforcement.authorize_stored_data_source_authoring`
    shape), so revoking the capability changes the very next attempt."""
    authorize_workprocess_registration(StoredCapabilityRegistry(session), user_id=user_id)
