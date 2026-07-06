"""Tests for HelpAdministration (WTK-106, REQ-043): configuring the help
system is the admin ``help.admin`` capability through the ONE capability
boundary; resolving help deliberately has no gate of its own."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from mentorapp.access import (
    ADMIN_CAPABILITIES,
    CAP_HELP_ADMIN,
    CapabilityError,
    InMemoryCapabilityRegistry,
    authorize_help_administration,
    authorize_stored_help_administration,
)
from mentorapp.storage import AccessGrant, AppUser, utcnow


def test_capability_vocabulary_names_the_admin_configuration_gate() -> None:
    # REQ-043: configuring the mapping/settings is the Administrator
    # persona's act — the key is an ADMIN capability. Resolving help
    # deliberately has NO capability: Help is never hidden.
    assert CAP_HELP_ADMIN == "help.admin"
    assert CAP_HELP_ADMIN in ADMIN_CAPABILITIES


def test_gate_admits_holders_and_refuses_everyone_else() -> None:
    holder = uuid.uuid4()
    other = uuid.uuid4()
    lookup = InMemoryCapabilityRegistry()
    lookup.add(holder, CAP_HELP_ADMIN)

    authorize_help_administration(lookup, user_id=holder)
    with pytest.raises(CapabilityError) as refused:
        authorize_help_administration(lookup, user_id=other)
    assert refused.value.capability == CAP_HELP_ADMIN


def test_stored_gate_reads_live_grant_rows_so_revocation_is_immediate(
    session: Session,
) -> None:
    admin = AppUser(crm_user_id="crm-admin.one", username="admin.one")
    session.add(admin)
    session.flush()
    grant = AccessGrant(user_id=admin.user_id, access_grant_key=CAP_HELP_ADMIN)
    session.add(grant)
    session.flush()

    authorize_stored_help_administration(session, user_id=admin.user_id)

    # Revocation is a soft delete; the very next attempt refuses — every
    # decision re-reads the grant rows, so no sweep is needed.
    grant.deleted_at = utcnow()
    session.flush()
    with pytest.raises(CapabilityError):
        authorize_stored_help_administration(session, user_id=admin.user_id)
