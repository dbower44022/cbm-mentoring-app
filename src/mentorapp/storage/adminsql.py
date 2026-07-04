"""Admin-authored SQL execution: read-only role isolation + injected row filtering.

Implements the DB-S9/REQ-056 admin-SQL rules. Admin-authored data sources run
SELECTs over the generated read views (``mentorapp.storage.readsurface``) —
never base tables — under a dedicated read-only database role with a
statement timeout. The role's grants are the real security boundary on
Postgres (SELECT on views only, no write verbs possible); the validation
here is defense in depth and the whole contract on dialects without roles.

userID filtering is injected, never trusted: a source declares itself
user-scoped, its SQL references ``:currentUserID``, and the executor binds
the session user's ID server-side. The author references the parameter but
can neither supply nor bypass it.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.orm import Session

from mentorapp.observability import get_logger

log = get_logger(__name__)

# The dedicated read-only role admin SQL executes under (Postgres). Provisioned
# by ops DDL (admin_sql_role_ddl); granted SELECT on generated views only.
ADMIN_SQL_ROLE: Final[str] = "mentorapp_admin_read"

# The timeout bounding the worst admin SQL can do: a slow query, cut off here.
ADMIN_SQL_STATEMENT_TIMEOUT_MS: Final[int] = 10_000

# The one server-bound parameter of user-scoped sources (DB-S9).
CURRENT_USER_PARAM: Final[str] = "currentUserID"

# Write/DDL/session verbs an admin source may never contain, as whole words —
# word boundaries keep camelCase column names (createdAt, modifiedAt) clean.
_FORBIDDEN_VERBS = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke"
    r"|copy|call|do|set|reset|vacuum|analyze|listen|notify|pragma|attach|detach)\b",
    re.IGNORECASE,
)


class AdminSqlError(ValueError):
    """An admin-authored source rejected before execution, with the reason."""


@dataclass(frozen=True)
class AdminSqlSource:
    """One admin-authored data source: its SQL and its declared scoping.

    ``user_scoped_flag`` is a declaration on the source, not on the request:
    the executor injects the session user for scoped sources on every run.
    Which staff roles may run a source is a separate per-source grant in app
    tables (the UI-standard security boundary), outside this module.
    """

    data_source_key: str
    sql_text: str
    user_scoped_flag: bool = False


def admin_sql_role_ddl(view_names: list[str]) -> list[str]:
    """The Postgres DDL provisioning the read-only role for the given views.

    Re-emitted alongside view regeneration so a new entity's view is granted
    the moment it exists; the blanket REVOKE keeps base tables unreachable
    even as the schema grows. Applied by ops/migration tooling, not at
    request time.
    """
    statements = [
        f"CREATE ROLE {ADMIN_SQL_ROLE} NOLOGIN",
        f"ALTER ROLE {ADMIN_SQL_ROLE} SET statement_timeout = "
        f"'{ADMIN_SQL_STATEMENT_TIMEOUT_MS}ms'",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ADMIN_SQL_ROLE}",
    ]
    statements.extend(
        f'GRANT SELECT ON "{view_name}" TO {ADMIN_SQL_ROLE}' for view_name in view_names
    )
    return statements


def validate_admin_sql(source: AdminSqlSource) -> None:
    """Reject a source that could be anything but one plain SELECT.

    Raises :class:`AdminSqlError` naming the violation. Comment tokens are
    rejected outright so the single-statement and verb checks cannot be
    smuggled past; admin sources have no business containing comments.
    """
    sql = source.sql_text.strip().rstrip(";").strip()
    if not sql:
        raise AdminSqlError(f"{source.data_source_key}: empty SQL")
    if "--" in sql or "/*" in sql:
        raise AdminSqlError(f"{source.data_source_key}: comment tokens are not allowed")
    if ";" in sql:
        raise AdminSqlError(f"{source.data_source_key}: one statement only")
    first_word = sql.split(None, 1)[0].lower()
    if first_word not in {"select", "with"}:
        raise AdminSqlError(f"{source.data_source_key}: must be a SELECT")
    if match := _FORBIDDEN_VERBS.search(sql):
        raise AdminSqlError(f"{source.data_source_key}: forbidden verb {match.group(0)!r}")
    references_user = f":{CURRENT_USER_PARAM}" in sql
    if source.user_scoped_flag and not references_user:
        raise AdminSqlError(
            f"{source.data_source_key}: user-scoped source must reference :{CURRENT_USER_PARAM}"
        )
    if not source.user_scoped_flag and references_user:
        raise AdminSqlError(
            f"{source.data_source_key}: :{CURRENT_USER_PARAM} requires the source "
            "to be declared user-scoped"
        )


def execute_admin_sql(
    session: Session,
    source: AdminSqlSource,
    *,
    current_user_id: uuid.UUID,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run one validated admin source and return its rows as plain dicts.

    On Postgres the statement executes as :data:`ADMIN_SQL_ROLE` with the
    statement timeout set for this transaction only (``SET LOCAL``); both are
    restored afterward so the caller's session keeps its own privileges. For
    user-scoped sources the session user is bound server-side; a caller
    supplying ``currentUserID`` in ``params`` is rejected, never merged.
    """
    validate_admin_sql(source)
    bound: dict[str, Any] = dict(params or {})
    if CURRENT_USER_PARAM in bound:
        raise AdminSqlError(
            f"{source.data_source_key}: {CURRENT_USER_PARAM} is bound server-side "
            "and cannot be supplied by the caller"
        )
    dialect_name = session.get_bind().dialect.name
    if source.user_scoped_flag:
        # The generic Uuid type stores dashless CHAR(32) where uuids are not
        # native, so the bound value must match that storage form.
        bound[CURRENT_USER_PARAM] = (
            current_user_id if dialect_name == "postgresql" else current_user_id.hex
        )

    if dialect_name == "postgresql":
        session.execute(text(f"SET LOCAL ROLE {ADMIN_SQL_ROLE}"))
        session.execute(
            text(f"SET LOCAL statement_timeout = '{ADMIN_SQL_STATEMENT_TIMEOUT_MS}ms'")
        )
    try:
        result = session.execute(text(source.sql_text), bound)
        rows = [dict(mapping) for mapping in result.mappings()]
    finally:
        if dialect_name == "postgresql":
            # SET LOCAL dies with the transaction, but the caller's transaction
            # is still open — hand its privileges back explicitly.
            session.execute(text("RESET ROLE"))
            session.execute(text("SET LOCAL statement_timeout TO DEFAULT"))
    log.info(
        "admin sql executed",
        extra={
            "context": {
                "dataSourceKey": source.data_source_key,
                "rowCount": len(rows),
                "userScoped": source.user_scoped_flag,
            }
        },
    )
    return rows
