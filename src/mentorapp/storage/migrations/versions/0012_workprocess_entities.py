"""Workprocess run + registration↔dataSource association (WTK-090, REQ-041/042).

The PI-005 backend rework of the workprocess storage: the shell's
``workprocessRegistration.targetDataSourceKeys`` soft references are replaced
by the real many-to-many ``workprocessRegistrationDataSource`` association —
``dataSource`` (0002) is a persisted table now, so the FK pair the shell
deferred exists to point at. The registration gains ``stepGraph`` (the
step-sequence declaration the REQ-042 execution frame walks), and
``workprocessRun`` lands: one launch, inheriting the selection
(``dataSourceID`` + ``selectedRecordIDs``), holding pending step answers as
JSON until commit — nothing commits until completion, and a cancelled run is
retained as a ``discarded`` row, never deleted. Same structural rules as
0001/0010: UUIDv7 app-generated keys (REQ-047), the eight structural system
columns (REQ-053), partial live-row indexes (REQ-052). Platform tables — no
schema-registry rows and no read views.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# Mirrors storage.base.JsonValue: JSONB on Postgres, plain JSON on SQLite.
_JSON_OBJECT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_LIVE = sa.text('"deletedAt" IS NULL')


def _structural_columns() -> list[sa.Column]:
    return [
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdBy", sa.Uuid(), nullable=True),
        sa.Column("modifiedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modifiedBy", sa.Uuid(), nullable=True),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletedBy", sa.Uuid(), nullable=True),
        sa.Column("rowVersion", sa.Integer(), nullable=False),
        sa.Column("customAttributes", _JSON_OBJECT, nullable=False),
    ]


def upgrade() -> None:
    with op.batch_alter_table("workprocessRegistration", schema=None) as batch_op:
        # server_default carries any pre-rework shell rows across the NOT NULL
        # add; the ORM default (dict) owns the value from here on.
        batch_op.add_column(
            sa.Column("stepGraph", _JSON_OBJECT, nullable=False, server_default=sa.text("'{}'"))
        )
        # The soft-reference key list the association table supersedes. The
        # rows it named were never authorable (no admin surface existed
        # before this rework), so there is no data to carry over — dropping
        # the column removes dead schema, not history.
        batch_op.drop_column("targetDataSourceKeys")

    op.create_table(
        "workprocessRegistrationDataSource",
        sa.Column("workprocessRegistrationDataSourceID", sa.Uuid(), nullable=False),
        sa.Column("workprocessRegistrationID", sa.Uuid(), nullable=False),
        sa.Column("dataSourceID", sa.Uuid(), nullable=False),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["workprocessRegistrationID"],
            ["workprocessRegistration.workprocessRegistrationID"],
            name=op.f(
                "fk_workprocessRegistrationDataSource_workprocessRegistrationID"
                "_workprocessRegistration"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["dataSourceID"],
            ["dataSource.dataSourceID"],
            name=op.f("fk_workprocessRegistrationDataSource_dataSourceID_dataSource"),
        ),
        sa.PrimaryKeyConstraint(
            "workprocessRegistrationDataSourceID",
            name=op.f("pk_workprocessRegistrationDataSource"),
        ),
    )
    with op.batch_alter_table("workprocessRegistrationDataSource", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_workprocessRegistrationDataSource_modifiedAt"),
            ["modifiedAt"],
            unique=False,
        )
        batch_op.create_index(
            "uq_workprocessRegistrationDataSource_pair_live",
            ["workprocessRegistrationID", "dataSourceID"],
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.create_index(
            "ix_workprocessRegistrationDataSource_dataSourceID_live",
            ["dataSourceID"],
            unique=False,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )

    op.create_table(
        "workprocessRun",
        sa.Column("workprocessRunID", sa.Uuid(), nullable=False),
        sa.Column("workprocessRegistrationID", sa.Uuid(), nullable=False),
        sa.Column("dataSourceID", sa.Uuid(), nullable=False),
        sa.Column("userID", sa.Uuid(), nullable=False),
        sa.Column("runState", sa.String(length=20), nullable=False),
        sa.Column("selectedRecordIDs", _JSON_OBJECT, nullable=False),
        sa.Column("stepAnswers", _JSON_OBJECT, nullable=False),
        sa.Column("currentStepKey", sa.String(length=100), nullable=True),
        sa.Column("completedAt", sa.DateTime(timezone=True), nullable=True),
        *_structural_columns(),
        sa.ForeignKeyConstraint(
            ["workprocessRegistrationID"],
            ["workprocessRegistration.workprocessRegistrationID"],
            name=op.f("fk_workprocessRun_workprocessRegistrationID_workprocessRegistration"),
        ),
        sa.ForeignKeyConstraint(
            ["dataSourceID"],
            ["dataSource.dataSourceID"],
            name=op.f("fk_workprocessRun_dataSourceID_dataSource"),
        ),
        sa.ForeignKeyConstraint(
            ["userID"], ["appUser.userID"], name=op.f("fk_workprocessRun_userID_appUser")
        ),
        sa.PrimaryKeyConstraint("workprocessRunID", name=op.f("pk_workprocessRun")),
    )
    with op.batch_alter_table("workprocessRun", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_workprocessRun_modifiedAt"), ["modifiedAt"], unique=False
        )
        batch_op.create_index(
            "ix_workprocessRun_registration_live",
            ["workprocessRegistrationID"],
            unique=False,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.create_index(
            "ix_workprocessRun_userID_live",
            ["userID"],
            unique=False,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )


def downgrade() -> None:
    with op.batch_alter_table("workprocessRun", schema=None) as batch_op:
        batch_op.drop_index(
            "ix_workprocessRun_userID_live", sqlite_where=_LIVE, postgresql_where=_LIVE
        )
        batch_op.drop_index(
            "ix_workprocessRun_registration_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_workprocessRun_modifiedAt"))
    op.drop_table("workprocessRun")

    with op.batch_alter_table("workprocessRegistrationDataSource", schema=None) as batch_op:
        batch_op.drop_index(
            "ix_workprocessRegistrationDataSource_dataSourceID_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(
            "uq_workprocessRegistrationDataSource_pair_live",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        )
        batch_op.drop_index(batch_op.f("ix_workprocessRegistrationDataSource_modifiedAt"))
    op.drop_table("workprocessRegistrationDataSource")

    with op.batch_alter_table("workprocessRegistration", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "targetDataSourceKeys",
                _JSON_OBJECT,
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.drop_column("stepGraph")
