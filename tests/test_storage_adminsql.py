"""Admin-SQL isolation design gate: validation + injected row filtering (WTK-131)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Mapped, Session, mapped_column

from mentorapp.storage import (
    ADMIN_SQL_ROLE,
    ADMIN_SQL_STATEMENT_TIMEOUT_MS,
    AdminSqlError,
    AdminSqlSource,
    BaseEntity,
    SchemaRegistry,
    admin_sql_role_ddl,
    entity_key,
    execute_admin_sql,
    regenerate_read_views,
    uuid7,
    validate_admin_sql,
)


class StaffNote(BaseEntity):
    __tablename__ = "StaffNote"

    staff_note_id: Mapped[uuid.UUID] = entity_key("staffNoteID")
    staff_note_text: Mapped[str] = mapped_column("staffNoteText", nullable=False)


def _select(sql: str, *, user_scoped: bool = False) -> AdminSqlSource:
    return AdminSqlSource(
        data_source_key="test.source", sql_text=sql, user_scoped_flag=user_scoped
    )


@pytest.mark.parametrize(
    "sql",
    [
        'DELETE FROM "vwStaffNote"',
        'SELECT 1; DROP TABLE "StaffNote"',
        'SELECT "staffNoteText" FROM "vwStaffNote" -- sneaky',
        "SELECT 1 /* smuggle */",
        "SELECT 1 WHERE 1 = 1 AND 2 IN (SELECT 2) FOR UPDATE OF x",
        'WITH x AS (SELECT 1) INSERT INTO "StaffNote" SELECT * FROM x',
        "",
    ],
)
def test_validation_rejects_anything_but_one_plain_select(sql: str) -> None:
    with pytest.raises(AdminSqlError):
        validate_admin_sql(_select(sql))


def test_validation_accepts_camel_case_columns_containing_verb_words() -> None:
    # createdAt/modifiedAt must not trip the create/update verb check.
    validate_admin_sql(_select('SELECT "createdAt", "modifiedAt" FROM "vwStaffNote"'))


def test_user_scoping_declaration_and_reference_must_agree() -> None:
    with pytest.raises(AdminSqlError):
        # Declared scoped but never references the parameter: filter would be lost.
        validate_admin_sql(_select('SELECT 1 FROM "vwStaffNote"', user_scoped=True))
    with pytest.raises(AdminSqlError):
        # References the parameter without declaring scoping.
        validate_admin_sql(
            _select('SELECT 1 FROM "vwStaffNote" WHERE "createdBy" = :currentUserID')
        )


def _register_staff_note(session: Session) -> None:
    session.add_all(
        [
            SchemaRegistry(
                entity_type="StaffNote",
                field_name="staffNoteID",
                field_type="id",
                field_label="Staff Note ID",
            ),
            SchemaRegistry(
                entity_type="StaffNote",
                field_name="staffNoteText",
                field_type="text",
                field_label="Text",
            ),
        ]
    )
    session.commit()


def test_user_scoped_source_gets_the_session_user_injected(session: Session) -> None:
    _register_staff_note(session)
    me, someone_else = uuid7(), uuid7()
    session.add_all(
        [
            StaffNote(staff_note_text="mine", created_by=me),
            StaffNote(staff_note_text="not mine", created_by=someone_else),
        ]
    )
    session.commit()
    regenerate_read_views(session)

    source = _select(
        'SELECT "staffNoteText" FROM "vwStaffNote" WHERE "createdBy" = :currentUserID',
        user_scoped=True,
    )
    rows = execute_admin_sql(session, source, current_user_id=me)
    assert rows == [{"staffNoteText": "mine"}]


def test_caller_cannot_supply_the_scoping_parameter(session: Session) -> None:
    source = _select(
        'SELECT "staffNoteText" FROM "vwStaffNote" WHERE "createdBy" = :currentUserID',
        user_scoped=True,
    )
    with pytest.raises(AdminSqlError):
        execute_admin_sql(
            session,
            source,
            current_user_id=uuid7(),
            params={"currentUserID": "someone-else"},
        )


def test_role_ddl_grants_views_only_with_timeout() -> None:
    statements = admin_sql_role_ddl(["vwStaffNote", "vwEngagement"])
    joined = "\n".join(statements)
    assert f"CREATE ROLE {ADMIN_SQL_ROLE} NOLOGIN" in joined
    assert f"'{ADMIN_SQL_STATEMENT_TIMEOUT_MS}ms'" in joined
    # Base tables are revoked wholesale; only the generated views are granted.
    assert f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ADMIN_SQL_ROLE}" in joined
    assert f'GRANT SELECT ON "vwStaffNote" TO {ADMIN_SQL_ROLE}' in joined
    assert f'GRANT SELECT ON "vwEngagement" TO {ADMIN_SQL_ROLE}' in joined
