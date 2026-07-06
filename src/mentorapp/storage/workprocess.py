"""Workprocess registration and run entities (WTK-090, REQ-041/REQ-042).

The workprocess framework's persistence: ``workprocessRegistration`` is one
administrator-registered custom multi-step application — registration is
DATA, not framework code (REQ-041): a live row surfaces the workprocess in
its target data sources' action lists, and no code change adds one.
``workprocessRun`` is one launch of a registered workprocess (REQ-042): it
inherits the launching selection and accumulates step answers as pending
JSONB state — NOTHING commits until completion, so the run row is the only
place an in-flight workprocess exists.

Associations: ``workprocessRegistrationDataSource`` is the many-to-many
registration ↔ dataSource association (which action lists carry the
workprocess) — a real FK pair now that ``dataSource`` is a persisted table
(WTK-001), replacing the shell's ``targetDataSourceKeys`` soft references;
``workprocessRun.workprocessRegistrationID`` is the many-to-one run ↔
registration association, and ``workprocessRun.dataSourceID`` names the ONE
data source the run launched from (the selection's home).

Permission is deliberately absent here (REQ-041): a registration carries no
grant columns because visibility is INHERITED from data-source access — the
REQ-006 boundary in :mod:`mentorapp.access.grants` is the only permission
model, and :mod:`mentorapp.access.workprocess` derives launchability from it.

This module is the one canonical home of the action vocabulary the grid
standard and workprocess registrations share: ``SELECTION_CONTRACTS`` (moved
from ``models.py`` with the entity) and ``ACTION_CLASSIFICATIONS`` (moved
from ``ui.grid_panel``) — ``mentorapp.ui`` imports them from storage (the
repo's vocab-sharing direction, e.g. the theming slot tuples) because the
reverse import would cycle through the ``ui`` package.

These are platform tables (``StructuralColumnsMixin`` + ``Base``, not
``BaseEntity``): like ``grid`` and ``dataSource`` they are app configuration
and run bookkeeping, get no schema-registry rows and no generated read
views. Every foreign-key column carries the exact name of the primary key it
references (DB-R2b).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import DateTime, ForeignKey, Index, String, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship, validates

from mentorapp.storage.auth import DataSource
from mentorapp.storage.base import Base, JsonValue, StructuralColumnsMixin, uuid7

# Same partial live-row predicate as models.py (DB-S3, REQ-052).
_LIVE = text('"deletedAt" IS NULL')

# REQ-041's selection contract: what row selection a workprocess needs from
# its host data source. App-validated vocabulary, never a database enum
# (DB-S7); ``ui.record_preview.PanelAction`` validates against THIS tuple.
SELECTION_CONTRACTS: Final[tuple[str, ...]] = ("none", "single", "multiple")

# REQ-041's action classification: where the action sorts in the host data
# source's action list and what confirmation weight it carries. One canonical
# home — grid panel actions and workprocess registrations both speak it.
ACTION_CLASSIFICATIONS: Final[tuple[str, ...]] = ("safe", "modifying", "destructive")

# REQ-042's run lifecycle. ``inFlight`` is the whole working life of a run —
# launch already shows the first step, so a separate pre-answer "draft" state
# would distinguish nothing the frame acts on. The two terminal states are
# the requirement's two exits: ``committed`` (completion — the one moment
# effects apply) and ``discarded`` (leave = cancel; the row survives as
# EVIDENCE that a run was abandoned — retained, never deleted).
RUN_STATE_IN_FLIGHT: Final = "inFlight"
RUN_STATE_COMMITTED: Final = "committed"
RUN_STATE_DISCARDED: Final = "discarded"

RUN_STATES: Final[tuple[str, ...]] = (
    RUN_STATE_IN_FLIGHT,
    RUN_STATE_COMMITTED,
    RUN_STATE_DISCARDED,
)

TERMINAL_RUN_STATES: Final[tuple[str, ...]] = (RUN_STATE_COMMITTED, RUN_STATE_DISCARDED)


class WorkprocessRegistration(StructuralColumnsMixin, Base):
    """An administrator-registered custom multi-step app (REQ-041).

    Registration is data, not framework code: a live row surfaces the
    workprocess in its target data sources' action lists; no code change
    adds one. Permission is inherited from data-source access — deliberately
    no per-app grant columns. ``selectionContract`` (``SELECTION_CONTRACTS``)
    drives the standard invalid-invocation explanations;
    ``actionClassification`` (``ACTION_CLASSIFICATIONS``) is the action-list
    grouping the grid standard declares.

    ``stepGraph`` is the step-sequence declaration the execution frame walks
    (REQ-042): ``{"startStepKey": …, "steps": [{"stepKey": …, "nextStepKey":
    … | null}, …]}``. A step's ``nextStepKey`` is its default successor
    (null = terminal); an ANSWER may name a different declared step, which is
    how earlier answers branch the sequence. Internal step content is the
    workprocess author's freedom — the framework validates only the graph
    shape (``automation.workprocess_engine.step_graph_problems``, wired at
    the API write), never what a step means.
    """

    __tablename__ = "workprocessRegistration"
    __table_args__ = (
        Index(
            "uq_workprocessRegistration_name_live",
            "workprocessName",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    workprocess_registration_id: Mapped[uuid.UUID] = mapped_column(
        "workprocessRegistrationID", primary_key=True, default=uuid7
    )
    workprocess_name: Mapped[str] = mapped_column(
        "workprocessName", String(200), nullable=False
    )
    workprocess_description: Mapped[str] = mapped_column(
        "workprocessDescription", String(2000), nullable=False
    )
    selection_contract: Mapped[str] = mapped_column(
        "selectionContract", String(50), nullable=False
    )
    action_classification: Mapped[str] = mapped_column(
        "actionClassification", String(100), nullable=False
    )
    step_graph: Mapped[dict[str, Any]] = mapped_column(
        "stepGraph", JsonValue, nullable=False, default=dict
    )

    # The registration ↔ dataSource association rows (the target list).
    data_source_links: Mapped[list[WorkprocessRegistrationDataSource]] = relationship(
        back_populates="registration"
    )
    runs: Mapped[list[WorkprocessRun]] = relationship(back_populates="registration")

    @validates("selection_contract")
    def _reject_unknown_contract(self, _key: str, value: str) -> str:
        # Persistence-boundary backstop of the DB-S7 app-validated vocabulary
        # (the theming @validates precedent): writers that never ride the API
        # surface (seeds, jobs, tests) cannot mint a contract the
        # invalid-invocation explainer has no words for.
        if value not in SELECTION_CONTRACTS:
            raise ValueError(
                f"selectionContract must be one of {SELECTION_CONTRACTS}, "
                f"got {value!r} (REQ-041)."
            )
        return value

    @validates("action_classification")
    def _reject_unknown_classification(self, _key: str, value: str) -> str:
        # Same backstop as selection_contract: the action list groups by this.
        if value not in ACTION_CLASSIFICATIONS:
            raise ValueError(
                f"actionClassification must be one of {ACTION_CLASSIFICATIONS}, "
                f"got {value!r} (REQ-041)."
            )
        return value


class WorkprocessRegistrationDataSource(StructuralColumnsMixin, Base):
    """The registration ↔ dataSource association: one target of one workprocess.

    A live row is what puts the workprocess in that data source's action
    list (REQ-041) — and, through the REQ-006 grant boundary on the SOURCE,
    what decides who sees it. Untargeting is a soft delete: the partial
    unique index lets the same pair be re-added later (the userCrmAccount
    association shape).
    """

    __tablename__ = "workprocessRegistrationDataSource"
    __table_args__ = (
        Index(
            "uq_workprocessRegistrationDataSource_pair_live",
            "workprocessRegistrationID",
            "dataSourceID",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
        # The action-list read leads with the SOURCE: "which workprocesses
        # target this data source"; the unique above leads with the
        # registration so it cannot serve this scan.
        Index(
            "ix_workprocessRegistrationDataSource_dataSourceID_live",
            "dataSourceID",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    workprocess_registration_data_source_id: Mapped[uuid.UUID] = mapped_column(
        "workprocessRegistrationDataSourceID", primary_key=True, default=uuid7
    )
    workprocess_registration_id: Mapped[uuid.UUID] = mapped_column(
        "workprocessRegistrationID",
        ForeignKey("workprocessRegistration.workprocessRegistrationID"),
        nullable=False,
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        "dataSourceID", ForeignKey("dataSource.dataSourceID"), nullable=False
    )

    registration: Mapped[WorkprocessRegistration] = relationship(
        back_populates="data_source_links"
    )
    data_source: Mapped[DataSource] = relationship()


class WorkprocessRun(StructuralColumnsMixin, Base):
    """One launch of a registered workprocess (REQ-042).

    The run inherits the launching selection — ``dataSourceID`` is the data
    source the action list belonged to, ``selectedRecordIDs`` the selected
    row identifiers (soft references: the records live in the read views /
    the CRM system of record, so there is nothing local to foreign-key).
    ``stepAnswers`` accumulates ``{stepKey: answer}`` as PENDING state and
    ``currentStepKey`` is where the walk stands: until the run commits, this
    row is the only place the workprocess's work exists, which is what makes
    "leave = cancel, discard everything" a single state flip (REQ-042).
    Discard keeps the row and its answers as evidence (``discarded`` state,
    ``completedAt`` stamped) — retention, never deletion. ``userID`` is the
    launching user: a run is one user's frame, so ownership is a domain
    fact, not audit metadata.
    """

    __tablename__ = "workprocessRun"
    __table_args__ = (
        # "Runs of this workprocess" — the registration's run history.
        Index(
            "ix_workprocessRun_registration_live",
            "workprocessRegistrationID",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
        # "This user's runs" — the ownership scan every run verb starts from.
        Index(
            "ix_workprocessRun_userID_live",
            "userID",
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    workprocess_run_id: Mapped[uuid.UUID] = mapped_column(
        "workprocessRunID", primary_key=True, default=uuid7
    )
    workprocess_registration_id: Mapped[uuid.UUID] = mapped_column(
        "workprocessRegistrationID",
        ForeignKey("workprocessRegistration.workprocessRegistrationID"),
        nullable=False,
    )
    # The launching data source — the selection's home and the grant the run
    # inherited (REQ-041/REQ-042).
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        "dataSourceID", ForeignKey("dataSource.dataSourceID"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        "userID", ForeignKey("appUser.userID"), nullable=False
    )
    run_state: Mapped[str] = mapped_column(
        "runState", String(20), nullable=False, default=RUN_STATE_IN_FLIGHT
    )
    # The inherited selection: record identifiers as text soft references.
    selected_record_ids: Mapped[list[str]] = mapped_column(
        "selectedRecordIDs", JsonValue, nullable=False, default=list
    )
    # {stepKey: answer} — pending until commit; retained after either exit.
    step_answers: Mapped[dict[str, Any]] = mapped_column(
        "stepAnswers", JsonValue, nullable=False, default=dict
    )
    # Where the walk stands; kept at its last value on a terminal run so the
    # evidence shows how far the run got.
    current_step_key: Mapped[str | None] = mapped_column(
        "currentStepKey", String(100), default=None
    )
    # Stamped by BOTH exits (committed and discarded): "how long did it run"
    # is a question about either ending.
    completed_at: Mapped[datetime | None] = mapped_column(
        "completedAt", DateTime(timezone=True), default=None
    )

    registration: Mapped[WorkprocessRegistration] = relationship(back_populates="runs")

    @validates("run_state")
    def _reject_unknown_state(self, _key: str, value: str) -> str:
        # Same persistence-boundary backstop as the registration vocabularies.
        if value not in RUN_STATES:
            raise ValueError(f"runState must be one of {RUN_STATES}, got {value!r} (REQ-042).")
        return value


def registrations_for_data_source(
    session: Session, data_source_key: str
) -> list[WorkprocessRegistration]:
    """The live registrations targeting the named source — the action-list read.

    Joined live-rows-only end to end (DB-S3): untargeting a source, retiring
    the source, or retiring the registration changes the very next action
    list with no sweep of dependents. Name-ordered so the list is stable.
    Permission is NOT decided here — the access layer gates the source
    itself (REQ-006 inheritance); this is only "what targets it".
    """
    return list(
        session.scalars(
            select(WorkprocessRegistration)
            .join(
                WorkprocessRegistrationDataSource,
                WorkprocessRegistrationDataSource.workprocess_registration_id
                == WorkprocessRegistration.workprocess_registration_id,
            )
            .join(
                DataSource,
                WorkprocessRegistrationDataSource.data_source_id == DataSource.data_source_id,
            )
            .where(
                DataSource.data_source_key == data_source_key,
                DataSource.deleted_at.is_(None),
                WorkprocessRegistrationDataSource.deleted_at.is_(None),
                WorkprocessRegistration.deleted_at.is_(None),
            )
            .order_by(WorkprocessRegistration.workprocess_name)
        )
    )
