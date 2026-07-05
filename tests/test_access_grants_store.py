"""Stored DataSourceAccessControl: persisted grants, revocation, row filter (WTK-007)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from mentorapp.access import (
    DataSourceAccessError,
    DataSourceNotFoundError,
    StoredGrantRegistry,
    grant_data_source_role,
    load_stored_source,
    revoke_data_source_role,
    run_stored_data_source,
)
from mentorapp.storage import AdminSqlError, DataSource, DataSourceRoleGrant, utcnow

MENTOR_USER = uuid.uuid4()
ADMIN_USER = uuid.uuid4()


@pytest.fixture()
def plain_source(session: Session) -> DataSource:
    source = DataSource(
        data_source_key="test.plain",
        data_source_name="Plain test source",
        data_source_sql="SELECT 1 AS answerValue",
    )
    session.add(source)
    session.flush()
    return source


@pytest.fixture()
def scoped_source(session: Session) -> DataSource:
    source = DataSource(
        data_source_key="test.scoped",
        data_source_name="Scoped test source",
        data_source_sql="SELECT :currentUserID AS scopedUserID",
        user_row_filter="scopedUserID",
    )
    session.add(source)
    session.flush()
    return source


def test_stored_grant_lets_role_holder_run_source(
    session: Session, plain_source: DataSource
) -> None:
    grant_data_source_role(
        session, data_source_key="test.plain", role_name="mentor", granted_by=ADMIN_USER
    )
    rows = run_stored_data_source(
        session,
        "test.plain",
        user_id=MENTOR_USER,
        user_roles=frozenset({"mentor", "staff"}),
    )
    assert rows == [{"answerValue": 1}]


def test_stored_source_without_grant_is_closed(
    session: Session, plain_source: DataSource
) -> None:
    with pytest.raises(DataSourceAccessError):
        run_stored_data_source(
            session,
            "test.plain",
            user_id=MENTOR_USER,
            user_roles=frozenset({"mentor", "admin", "staff"}),
        )


def test_unknown_source_key_is_denied_not_revealed(session: Session) -> None:
    # A denied caller and a caller probing for keys see the same refusal:
    # access error, never not-found.
    with pytest.raises(DataSourceAccessError):
        run_stored_data_source(
            session,
            "test.absent",
            user_id=MENTOR_USER,
            user_roles=frozenset({"mentor"}),
        )


def test_revoking_grant_removes_access_for_dependents(
    session: Session, plain_source: DataSource
) -> None:
    # Every dependent surface (panel, view, export) reaches the data through
    # run_stored_data_source, so revocation takes effect on the very next run.
    grant_data_source_role(session, data_source_key="test.plain", role_name="mentor")
    assert run_stored_data_source(
        session, "test.plain", user_id=MENTOR_USER, user_roles=frozenset({"mentor"})
    ) == [{"answerValue": 1}]

    assert revoke_data_source_role(
        session, data_source_key="test.plain", role_name="mentor", revoked_by=ADMIN_USER
    )
    with pytest.raises(DataSourceAccessError):
        run_stored_data_source(
            session, "test.plain", user_id=MENTOR_USER, user_roles=frozenset({"mentor"})
        )


def test_revoke_is_soft_delete_and_regrant_restores_access(
    session: Session, plain_source: DataSource
) -> None:
    first = grant_data_source_role(session, data_source_key="test.plain", role_name="mentor")
    revoke_data_source_role(session, data_source_key="test.plain", role_name="mentor")
    session.expire_all()

    revoked = session.get(DataSourceRoleGrant, first.data_source_role_grant_id)
    assert revoked is not None
    assert revoked.deleted_at is not None

    second = grant_data_source_role(session, data_source_key="test.plain", role_name="mentor")
    assert second.data_source_role_grant_id != first.data_source_role_grant_id
    assert StoredGrantRegistry(session).roles_granted("test.plain") == frozenset({"mentor"})


def test_grant_is_idempotent_while_live(session: Session, plain_source: DataSource) -> None:
    first = grant_data_source_role(session, data_source_key="test.plain", role_name="mentor")
    second = grant_data_source_role(session, data_source_key="test.plain", role_name="mentor")
    assert second.data_source_role_grant_id == first.data_source_role_grant_id


def test_grant_on_unknown_source_is_refused(session: Session) -> None:
    with pytest.raises(DataSourceNotFoundError):
        grant_data_source_role(session, data_source_key="test.absent", role_name="mentor")


def test_revoke_without_live_grant_reports_nothing_revoked(
    session: Session, plain_source: DataSource
) -> None:
    assert not revoke_data_source_role(
        session, data_source_key="test.plain", role_name="mentor"
    )


def test_stored_row_filter_binds_the_session_user(
    session: Session, scoped_source: DataSource
) -> None:
    grant_data_source_role(session, data_source_key="test.scoped", role_name="mentor")
    rows = run_stored_data_source(
        session, "test.scoped", user_id=MENTOR_USER, user_roles=frozenset({"mentor"})
    )
    assert rows == [{"scopedUserID": MENTOR_USER.hex}]


def test_stored_row_filter_cannot_be_bypassed_by_caller_params(
    session: Session, scoped_source: DataSource
) -> None:
    grant_data_source_role(session, data_source_key="test.scoped", role_name="mentor")
    with pytest.raises(AdminSqlError, match="bound server-side"):
        run_stored_data_source(
            session,
            "test.scoped",
            user_id=MENTOR_USER,
            user_roles=frozenset({"mentor"}),
            params={"currentUserID": uuid.uuid4()},
        )


def test_load_stored_source_carries_the_scoping_declaration(
    session: Session, plain_source: DataSource, scoped_source: DataSource
) -> None:
    # userRowFilter on the row IS the user-scoped flag; a caller cannot
    # re-describe a stored source as unscoped.
    assert load_stored_source(session, "test.plain").user_scoped_flag is False
    assert load_stored_source(session, "test.scoped").user_scoped_flag is True
    with pytest.raises(DataSourceNotFoundError):
        load_stored_source(session, "test.absent")


def test_retired_source_is_closed_even_with_live_grant(
    session: Session, plain_source: DataSource
) -> None:
    grant_data_source_role(session, data_source_key="test.plain", role_name="mentor")
    plain_source.deleted_at = utcnow()
    session.flush()
    with pytest.raises(DataSourceAccessError):
        run_stored_data_source(
            session, "test.plain", user_id=MENTOR_USER, user_roles=frozenset({"mentor"})
        )
