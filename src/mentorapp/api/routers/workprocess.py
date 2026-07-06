"""``/workprocesses`` — registration CRUD, action lists, and the run frame (WTK-092).

REQ-041 over the wire: registrations are admin-gated data (the persisted
``workprocess.register`` capability; a 403 for everyone else), and the
action-list read a grid's Other Actions menu consumes is permission-
INHERITED — :func:`~mentorapp.access.workprocess.visible_workprocesses`
serves a source's whole list to anyone whose roles cover the source and an
empty list otherwise, so nothing here re-decides access and nothing is
trimmed per registration (never hide; restriction happens by tighter data
sources). Entries speak the grid standard's action vocabulary
(``label``/``selectionContract``/``classification`` — the ``PanelAction``
wire shape) so the menu renders workprocesses like any declared action.

REQ-042 as four run verbs over the engine
(:mod:`mentorapp.automation.workprocess_engine` owns the frame; this router
only speaks it):

- ``POST /workprocesses/runs`` launches from an action list, inheriting the
  selection. A selection that violates the registration's contract refuses
  in the educate voice through the grid standard's ONE explainer
  (:func:`~mentorapp.ui.grid_panel.invalid_invocation`) — the same words a
  panel action gives the same mistake.
- ``POST …/runs/{id}/step`` answers the current step; the answer may name a
  declared ``nextStepKey`` to branch. Frame refusals (wrong step, ended
  run) are 422 educate messages keyed by stable codes.
- ``POST …/runs/{id}/commit`` is the one applying moment: the engine hands
  the payload to the registration's own handler (the wiring-bound
  :class:`~mentorapp.automation.workprocess_engine.CommitHandlerRegistry`)
  and flips the run, atomically. The response confirms success and
  ``meta.affectedDataSourceKeys`` names the grids to refresh.
- ``POST …/runs/{id}/cancel`` discards: nothing persists except the run row
  marked ``discarded`` — evidence, not deletion.

Runs are the launching user's own frame: another user's run answers the
same 404 as one that never existed, so run IDs cannot be probed. Roles ride
the fail-loud provider seam (:func:`get_role_source`, the shell-catalog
pattern) until session-derived roles are wired.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.access.workprocess import (
    WorkprocessNotTargetedError,
    authorize_stored_workprocess_registration,
    authorize_workprocess_launch,
    visible_workprocesses,
)
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.envelope import ApiError, Envelope, field_error, ok
from mentorapp.api.errors import ApiValidationError, RecordNotFoundError, StaleRowVersionError
from mentorapp.automation.workprocess_engine import (
    LONG_RUN_NOTIFICATION_AFTER,
    CommitHandlerRegistry,
    InMemoryCommitHandlers,
    NotCurrentStepError,
    RunNotCompletableError,
    RunNotInFlightError,
    UnknownBranchError,
    WorkprocessRunError,
    answer_step,
    cancel_run,
    commit_run,
    launch_run,
    step_graph_problems,
)
from mentorapp.observability import get_logger
from mentorapp.storage import (
    ACTION_CLASSIFICATIONS,
    SELECTION_CONTRACTS,
    DataSource,
    WorkprocessRegistration,
    WorkprocessRegistrationDataSource,
    WorkprocessRun,
    as_utc,
    utcnow,
)

# The api → ui import direction is established (the theming router's
# precedent): the grid standard's explainer is the ONE voice for a
# selection-contract mistake, consumed here rather than re-worded.
from mentorapp.ui.grid_panel import invalid_invocation
from mentorapp.ui.record_preview import PanelAction

log = get_logger(__name__)

router = APIRouter()

_REGISTRATION_ENTITY = "workprocessRegistration"
_RUN_ENTITY = "workprocessRun"

# Stable machine-readable codes (clients switch on these; adding is additive).
CODE_UNKNOWN_SELECTION_CONTRACT = "unknownSelectionContract"
CODE_UNKNOWN_ACTION_CLASSIFICATION = "unknownActionClassification"
CODE_UNKNOWN_DATA_SOURCE = "unknownDataSource"
CODE_DUPLICATE_WORKPROCESS_NAME = "duplicateWorkprocessName"
CODE_INVALID_STEP_GRAPH = "invalidStepGraph"
CODE_SELECTION_CONTRACT_VIOLATION = "selectionContractViolation"
CODE_RUN_NOT_IN_FLIGHT = "runNotInFlight"
CODE_NOT_CURRENT_STEP = "notCurrentStep"
CODE_UNKNOWN_BRANCH = "unknownBranch"
CODE_RUN_NOT_COMPLETABLE = "runNotCompletable"


class RoleSource(Protocol):
    """The session-roles seam the inherited-permission reads consume.

    Roles are session-scoped, captured from the CRM at login (WTK-001) —
    this router never owns a role table any more than the shell does; the
    same seam shape as ``ShellCatalog.user_roles``.
    """

    def user_roles(self, user_id: uuid.UUID) -> frozenset[str]:
        """The user's role names, for the REQ-006 grant decision."""
        ...


def get_role_source() -> RoleSource:
    """Provide the role source; wiring binds it, tests override it.

    Fail-loud, never an empty default: an empty role set would render every
    action list empty and every launch denied — a deployment error must not
    read as a universal permission denial.
    """
    raise RuntimeError(
        "role source provider is not wired; install workprocess wiring or "
        "override get_role_source."
    )


# Module-level default: commits complete without any bound handler (REQ-041 —
# an admin registers a workprocess with NO framework code change; the run row
# is the durable outcome). Wiring overrides this with the app's registry that
# binds each workprocess's own handler.
_UNBOUND_HANDLERS = InMemoryCommitHandlers()


def get_commit_handlers() -> CommitHandlerRegistry:
    """Provide the per-workprocess commit-handler registry (the engine seam).

    Deliberately NOT fail-loud (unlike :func:`get_role_source`): "no handler
    bound" is a sanctioned state the engine logs and absorbs, not a broken
    deployment — refusing every commit until code ships would contradict
    REQ-041's registrations-without-code-changes.
    """
    return _UNBOUND_HANDLERS


_SessionDep = Annotated[Session, Depends(get_session)]
_UserDep = Annotated[uuid.UUID, Depends(get_current_user_id)]
_RolesDep = Annotated[RoleSource, Depends(get_role_source)]
_HandlersDep = Annotated[CommitHandlerRegistry, Depends(get_commit_handlers)]


# --- Payloads ------------------------------------------------------------------------


def _live_target_keys(registration: WorkprocessRegistration) -> list[str]:
    return sorted(
        link.data_source.data_source_key
        for link in registration.data_source_links
        if link.deleted_at is None and link.data_source.deleted_at is None
    )


def _registration_payload(registration: WorkprocessRegistration) -> dict[str, Any]:
    return {
        "workprocessRegistrationID": registration.workprocess_registration_id,
        "workprocessName": registration.workprocess_name,
        "workprocessDescription": registration.workprocess_description,
        "targetDataSourceKeys": _live_target_keys(registration),
        "selectionContract": registration.selection_contract,
        "actionClassification": registration.action_classification,
        "stepGraph": registration.step_graph,
        "rowVersion": registration.row_version,
    }


def _run_payload(run: WorkprocessRun) -> dict[str, Any]:
    return {
        "workprocessRunID": run.workprocess_run_id,
        "workprocessRegistrationID": run.workprocess_registration_id,
        "runState": run.run_state,
        "selectedRecordIDs": list(run.selected_record_ids),
        "stepAnswers": dict(run.step_answers),
        "currentStepKey": run.current_step_key,
        # The frame's "you can commit now" fact: the walk resolved to a
        # terminal step and the run is still open.
        "completable": run.current_step_key is None and run.run_state == "inFlight",
        "rowVersion": run.row_version,
    }


# --- Registration CRUD (admin-gated, REQ-041) ---------------------------------------


def _live_registration(session: Session, registration_id: uuid.UUID) -> WorkprocessRegistration:
    registration = session.get(WorkprocessRegistration, registration_id)
    if registration is None or registration.deleted_at is not None:
        raise RecordNotFoundError(_REGISTRATION_ENTITY, str(registration_id))
    return registration


def _vocabulary_errors(selection_contract: str, action_classification: str) -> list[ApiError]:
    errors: list[ApiError] = []
    if selection_contract not in SELECTION_CONTRACTS:
        errors.append(
            field_error(
                "selectionContract",
                CODE_UNKNOWN_SELECTION_CONTRACT,
                f"'{selection_contract}' is not a selection contract; the "
                f"contracts are {', '.join(SELECTION_CONTRACTS)}.",
            )
        )
    if action_classification not in ACTION_CLASSIFICATIONS:
        errors.append(
            field_error(
                "actionClassification",
                CODE_UNKNOWN_ACTION_CLASSIFICATION,
                f"'{action_classification}' is not an action classification; "
                f"the classifications are {', '.join(ACTION_CLASSIFICATIONS)}.",
            )
        )
    return errors


def _step_graph_errors(document: Any) -> list[ApiError]:
    return [
        field_error("stepGraph", CODE_INVALID_STEP_GRAPH, problem)
        for problem in step_graph_problems(document)
    ]


def _resolve_target_sources(
    session: Session, keys: list[str]
) -> tuple[dict[str, DataSource], list[ApiError]]:
    """The live ``dataSource`` rows the target keys name, plus one error per miss.

    A registration on a source that does not exist would be an action list
    nobody can ever see — an admin mistake caught at write time (the
    grant-on-nothing rule in ``access.grants``).
    """
    rows = {
        source.data_source_key: source
        for source in session.scalars(
            select(DataSource).where(
                DataSource.data_source_key.in_(keys), DataSource.deleted_at.is_(None)
            )
        )
    }
    errors = [
        field_error(
            "targetDataSourceKeys",
            CODE_UNKNOWN_DATA_SOURCE,
            f"'{key}' names no data source; workprocesses register against "
            f"existing data sources.",
        )
        for key in keys
        if key not in rows
    ]
    return rows, errors


def _duplicate_name_errors(
    session: Session, name: str, *, exclude_id: uuid.UUID | None = None
) -> list[ApiError]:
    query = (
        select(WorkprocessRegistration)
        .where(WorkprocessRegistration.deleted_at.is_(None))
        .where(WorkprocessRegistration.workprocess_name == name)
    )
    if exclude_id is not None:
        query = query.where(WorkprocessRegistration.workprocess_registration_id != exclude_id)
    if session.scalars(query).first() is None:
        return []
    return [
        field_error(
            "workprocessName",
            CODE_DUPLICATE_WORKPROCESS_NAME,
            f"a live workprocess named {name!r} already exists; display "
            f"names are the action-list identity.",
        )
    ]


@router.get("/workprocesses")
def list_registrations(session: _SessionDep, user_id: _UserDep) -> Envelope:
    """The admin management list: every live registration with its targets.

    Admin-gated like every registration verb — this is the management
    surface; what a USER sees is the per-source action-list read below.
    """
    authorize_stored_workprocess_registration(session, user_id=user_id)
    registrations = session.scalars(
        select(WorkprocessRegistration)
        .where(WorkprocessRegistration.deleted_at.is_(None))
        .order_by(WorkprocessRegistration.workprocess_name)
    ).all()
    return ok(data=[_registration_payload(r) for r in registrations])


class RegistrationCreateBody(BaseModel):
    """POST body: REQ-041's whole declaration in one document."""

    model_config = ConfigDict(extra="forbid")

    workprocess_name: str = Field(alias="workprocessName", min_length=1, max_length=200)
    workprocess_description: str = Field(
        alias="workprocessDescription", min_length=1, max_length=2000
    )
    target_data_source_keys: list[str] = Field(alias="targetDataSourceKeys", min_length=1)
    selection_contract: str = Field(alias="selectionContract")
    action_classification: str = Field(alias="actionClassification")
    step_graph: dict[str, Any] = Field(alias="stepGraph")


@router.post("/workprocesses")
def create_registration(
    body: RegistrationCreateBody, session: _SessionDep, user_id: _UserDep
) -> Envelope:
    """Register a workprocess (REQ-041) — the Administrator persona's act.

    Every gate in one round trip (DB-S12): vocabulary membership, the step
    graph's structural problems, target keys that resolve to live data
    sources, and the live-name uniqueness. The registration is data — the
    action appears in the targeted sources' lists with NO framework change.
    """
    authorize_stored_workprocess_registration(session, user_id=user_id)
    sources, source_errors = _resolve_target_sources(session, body.target_data_source_keys)
    errors = (
        _vocabulary_errors(body.selection_contract, body.action_classification)
        + _step_graph_errors(body.step_graph)
        + source_errors
        + _duplicate_name_errors(session, body.workprocess_name)
    )
    if errors:
        raise ApiValidationError(errors)
    registration = WorkprocessRegistration(
        workprocess_name=body.workprocess_name,
        workprocess_description=body.workprocess_description,
        selection_contract=body.selection_contract,
        action_classification=body.action_classification,
        step_graph=body.step_graph,
        created_by=user_id,
        modified_by=user_id,
    )
    session.add(registration)
    session.flush()
    for key in dict.fromkeys(body.target_data_source_keys):
        session.add(
            WorkprocessRegistrationDataSource(
                workprocess_registration_id=registration.workprocess_registration_id,
                data_source_id=sources[key].data_source_id,
                created_by=user_id,
                modified_by=user_id,
            )
        )
    session.commit()
    log.info(
        "workprocess registered",
        extra={
            "context": {
                "workprocessRegistrationID": str(registration.workprocess_registration_id),
                "userID": str(user_id),
                "targetCount": len(set(body.target_data_source_keys)),
            }
        },
    )
    return ok(data=_registration_payload(registration))


@router.get("/workprocesses/{registration_id}")
def get_registration(
    registration_id: uuid.UUID, session: _SessionDep, user_id: _UserDep
) -> Envelope:
    """One registration, whole (admin management read)."""
    authorize_stored_workprocess_registration(session, user_id=user_id)
    return ok(data=_registration_payload(_live_registration(session, registration_id)))


class RegistrationPatchBody(BaseModel):
    """PATCH body: only the changed fields plus the mandatory ``rowVersion``.

    ``targetDataSourceKeys`` replaces WHOLE (the target list is one fact,
    like a slot document): removed targets soft-delete their association
    rows, added ones insert fresh rows.
    """

    model_config = ConfigDict(extra="forbid")

    row_version: int = Field(alias="rowVersion")
    workprocess_name: str | None = Field(
        default=None, alias="workprocessName", min_length=1, max_length=200
    )
    workprocess_description: str | None = Field(
        default=None, alias="workprocessDescription", min_length=1, max_length=2000
    )
    target_data_source_keys: list[str] | None = Field(
        default=None, alias="targetDataSourceKeys", min_length=1
    )
    selection_contract: str | None = Field(default=None, alias="selectionContract")
    action_classification: str | None = Field(default=None, alias="actionClassification")
    step_graph: dict[str, Any] | None = Field(default=None, alias="stepGraph")


def _retarget(
    session: Session,
    registration: WorkprocessRegistration,
    sources: dict[str, DataSource],
    keys: list[str],
    user_id: uuid.UUID,
) -> None:
    """Reconcile the association rows to the new whole target list.

    Removal is a soft delete (the association survives as targeting
    history); re-adding a previously removed pair inserts a fresh row — the
    partial unique index frees the pair the moment the old row died.
    """
    wanted = set(keys)
    now = utcnow()
    for link in registration.data_source_links:
        if link.deleted_at is not None:
            continue
        if link.data_source.data_source_key not in wanted:
            link.deleted_at = now
            link.deleted_by = user_id
            link.modified_at = now
            link.modified_by = user_id
        else:
            wanted.discard(link.data_source.data_source_key)
    for key in wanted:
        session.add(
            WorkprocessRegistrationDataSource(
                workprocess_registration_id=registration.workprocess_registration_id,
                data_source_id=sources[key].data_source_id,
                created_by=user_id,
                modified_by=user_id,
            )
        )


@router.patch("/workprocesses/{registration_id}")
def patch_registration(
    registration_id: uuid.UUID,
    body: RegistrationPatchBody,
    session: _SessionDep,
    user_id: _UserDep,
) -> Envelope:
    """Per-field registration edit under the write contract (DB-S12, DB-S4)."""
    authorize_stored_workprocess_registration(session, user_id=user_id)
    registration = _live_registration(session, registration_id)
    if body.row_version != registration.row_version:
        raise StaleRowVersionError(_registration_payload(registration))
    sent = body.model_fields_set
    errors: list[ApiError] = []
    sources: dict[str, DataSource] = {}
    if "selection_contract" in sent or "action_classification" in sent:
        errors += _vocabulary_errors(
            body.selection_contract
            if "selection_contract" in sent and body.selection_contract is not None
            else registration.selection_contract,
            body.action_classification
            if "action_classification" in sent and body.action_classification is not None
            else registration.action_classification,
        )
    if "step_graph" in sent and body.step_graph is not None:
        errors += _step_graph_errors(body.step_graph)
    if "target_data_source_keys" in sent and body.target_data_source_keys is not None:
        sources, source_errors = _resolve_target_sources(session, body.target_data_source_keys)
        errors += source_errors
    if "workprocess_name" in sent and body.workprocess_name is not None:
        errors += _duplicate_name_errors(
            session, body.workprocess_name, exclude_id=registration_id
        )
    if errors:
        raise ApiValidationError(errors)
    for attr in (
        "workprocess_name",
        "workprocess_description",
        "selection_contract",
        "action_classification",
        "step_graph",
    ):
        if attr in sent and getattr(body, attr) is not None:
            setattr(registration, attr, getattr(body, attr))
    if "target_data_source_keys" in sent and body.target_data_source_keys is not None:
        _retarget(session, registration, sources, body.target_data_source_keys, user_id)
    registration.modified_by = user_id
    registration.modified_at = utcnow()
    session.commit()
    return ok(data=_registration_payload(registration))


@router.delete("/workprocesses/{registration_id}")
def delete_registration(
    registration_id: uuid.UUID, session: _SessionDep, user_id: _UserDep
) -> Envelope:
    """Retire a registration (DB-S3 soft delete) and its target links with it.

    A target link is meaningless without its registration, so the cascade is
    this relationship's declared behavior (the theming template/rule shape).
    The action disappears from every targeted source's list on the next
    read; run rows are history and stay untouched.
    """
    authorize_stored_workprocess_registration(session, user_id=user_id)
    registration = _live_registration(session, registration_id)
    now = utcnow()
    live_links = [link for link in registration.data_source_links if link.deleted_at is None]
    for record in (registration, *live_links):
        record.deleted_at = now
        record.deleted_by = user_id
        record.modified_at = now
        record.modified_by = user_id
    session.commit()
    log.info(
        "workprocess registration retired",
        extra={
            "context": {
                "workprocessRegistrationID": str(registration_id),
                "userID": str(user_id),
                "targetsRetired": len(live_links),
            }
        },
    )
    return ok(
        data={
            "workprocessRegistrationID": registration_id,
            "deleted": True,
            "targetsRetired": len(live_links),
        }
    )


# --- The action-list read (what the Other Actions menu consumes, REQ-041) -----------


@router.get("/workprocesses/actions/{data_source_key}")
def list_actions(
    data_source_key: str, session: _SessionDep, user_id: _UserDep, roles: _RolesDep
) -> Envelope:
    """One data source's workprocess action-list entries for the caller.

    The inherited-permission read in its quiet form: covered source → every
    live registration targeting it, in the ``PanelAction`` wire shape the
    grid's menus already speak; uncovered or unknown source → an empty list
    (the caller does not see that action list at all, and the two answers
    are identical so source keys cannot be probed).
    """
    entries = visible_workprocesses(
        session, data_source_key=data_source_key, user_roles=roles.user_roles(user_id)
    )
    return ok(
        data=[
            {
                "workprocessRegistrationID": r.workprocess_registration_id,
                "label": r.workprocess_name,
                "description": r.workprocess_description,
                "selectionContract": r.selection_contract,
                "classification": r.action_classification,
            }
            for r in entries
        ],
        meta={"totalCount": len(entries)},
    )


# --- The run frame (REQ-042) ---------------------------------------------------------


def _own_run(
    session: Session, run_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[WorkprocessRun, WorkprocessRegistration]:
    """The caller's live run plus its registration, or the uniform 404.

    Another user's run answers exactly like one that never existed — a run
    is one user's frame, and run IDs must not be probeable.
    """
    run = session.get(WorkprocessRun, run_id)
    if run is None or run.deleted_at is not None or run.user_id != user_id:
        raise RecordNotFoundError(_RUN_ENTITY, str(run_id))
    return run, run.registration


def _selection_contract_refusal(
    registration: WorkprocessRegistration, selected_count: int
) -> None:
    """Refuse a launch whose selection violates the contract — educate voice.

    The grid standard's ONE explainer (``invalid_invocation``) provides the
    words: the workprocess presents as a panel action, so the same mistake
    gets the same what/why/what-next a built-in action gives it.
    """
    action = PanelAction(
        key=str(registration.workprocess_registration_id),
        label=registration.workprocess_name,
        selection_contract=registration.selection_contract,
        classification=registration.action_classification,
    )
    message = invalid_invocation(action, selected_count)
    if message is None:
        return
    raise ApiValidationError(
        [
            field_error(
                "selectedRecordIDs",
                CODE_SELECTION_CONTRACT_VIOLATION,
                f"{message.what_happened} {message.why} {message.what_next}",
            )
        ]
    )


class RunLaunchBody(BaseModel):
    """POST body: which workprocess, from which action list, over which rows."""

    model_config = ConfigDict(extra="forbid")

    workprocess_registration_id: uuid.UUID = Field(alias="workprocessRegistrationID")
    data_source_key: str = Field(alias="dataSourceKey", min_length=1)
    selected_record_ids: list[str] = Field(default_factory=list, alias="selectedRecordIDs")


@router.post("/workprocesses/runs")
def launch(
    body: RunLaunchBody, session: _SessionDep, user_id: _UserDep, roles: _RolesDep
) -> Envelope:
    """Launch a run from an action list, inheriting the selection (REQ-042).

    Gates in order: the registration exists (404); the launch is sanctioned
    — targeted source AND the caller's inherited data-source access (the
    not-targeted case answers the SAME 404, so pairings cannot be probed;
    a grant miss is the standard 403); the selection fits the contract
    (422, educate voice). Then the frame opens on the graph's start step.
    """
    registration = _live_registration(session, body.workprocess_registration_id)
    try:
        authorize_workprocess_launch(
            session,
            registration,
            data_source_key=body.data_source_key,
            user_id=user_id,
            user_roles=roles.user_roles(user_id),
        )
    except WorkprocessNotTargetedError as not_targeted:
        raise RecordNotFoundError(
            _REGISTRATION_ENTITY, str(body.workprocess_registration_id)
        ) from not_targeted
    _selection_contract_refusal(registration, len(body.selected_record_ids))
    source = session.scalars(
        select(DataSource).where(
            DataSource.data_source_key == body.data_source_key,
            DataSource.deleted_at.is_(None),
        )
    ).one()
    run = launch_run(
        session,
        registration,
        data_source_id=source.data_source_id,
        selected_record_ids=body.selected_record_ids,
        user_id=user_id,
    )
    session.commit()
    return ok(data=_run_payload(run))


def _run_error_envelope(exc: WorkprocessRunError) -> ApiValidationError:
    """One educate-voice 422 per frame refusal, keyed by a stable code.

    Frame refusals are user mistakes to explain (REQ-042's never-hide
    ethos), not access denials: what happened, why the frame refused, and
    what to do instead.
    """
    if isinstance(exc, RunNotInFlightError):
        error = field_error(
            "workprocessRunID",
            CODE_RUN_NOT_IN_FLIGHT,
            f"This run already ended ({exc.run_state}). A run accepts steps "
            f"only while it is open — launch the workprocess again to start "
            f"a new one.",
        )
    elif isinstance(exc, NotCurrentStepError):
        error = field_error(
            "stepKey",
            CODE_NOT_CURRENT_STEP,
            f"'{exc.step_key}' is not the step this run is on. The run "
            f"stands on '{exc.current_step_key}' — answer that step; the "
            f"sequence decides what comes next.",
        )
    elif isinstance(exc, UnknownBranchError):
        error = field_error(
            "nextStepKey",
            CODE_UNKNOWN_BRANCH,
            f"'{exc.next_step_key}' names no step of this workprocess. An "
            f"answer may only route to a step the registration declares.",
        )
    elif isinstance(exc, RunNotCompletableError):
        error = field_error(
            "workprocessRunID",
            CODE_RUN_NOT_COMPLETABLE,
            f"This run isn't finished — it still stands on "
            f"'{exc.current_step_key}'. Answer the remaining steps; nothing "
            f"is applied until the run completes.",
        )
    else:  # pragma: no cover — every subclass is mapped above.
        raise exc
    return ApiValidationError([error])


class RunStepBody(BaseModel):
    """POST body: the current step's answer, optionally routing the branch."""

    model_config = ConfigDict(extra="forbid")

    step_key: str = Field(alias="stepKey", min_length=1)
    # The answer is the author's document — the frame stores it verbatim.
    answer: Any = None
    next_step_key: str | None = Field(default=None, alias="nextStepKey")


@router.post("/workprocesses/runs/{run_id}/step")
def step(
    run_id: uuid.UUID, body: RunStepBody, session: _SessionDep, user_id: _UserDep
) -> Envelope:
    """Answer the run's current step; the answer may branch (REQ-042).

    The answer lands in the run's pending state only — no application data
    moves until commit. The response says where the walk now stands
    (``currentStepKey``; ``completable`` once a terminal step resolved).
    """
    run, registration = _own_run(session, run_id, user_id)
    try:
        answer_step(
            session,
            run,
            registration,
            step_key=body.step_key,
            answer=body.answer,
            next_step_key=body.next_step_key,
        )
    except WorkprocessRunError as refused:
        raise _run_error_envelope(refused) from refused
    session.commit()
    return ok(data=_run_payload(run))


@router.post("/workprocesses/runs/{run_id}/commit")
def commit(
    run_id: uuid.UUID, session: _SessionDep, user_id: _UserDep, handlers: _HandlersDep
) -> Envelope:
    """Complete the run: apply everything, atomically, and confirm (REQ-042).

    The engine hands the payload to the registration's own handler and flips
    the run in one transaction — a raising handler aborts both. The
    confirmation names the workprocess; ``meta.affectedDataSourceKeys`` is
    the refresh list for the grids the workprocess targets, and
    ``meta.ranLong`` says whether the ran-long bell entry was written.
    """
    run, registration = _own_run(session, run_id, user_id)
    # get_one: the run's source is a real FK, so a miss is a broken database
    # and must surface as the loud 500, never a silent empty key.
    source = session.get_one(DataSource, run.data_source_id)
    now = utcnow()
    try:
        commit_run(
            session,
            run,
            registration,
            data_source_key=source.data_source_key,
            handlers=handlers,
            now=now,
        )
    except WorkprocessRunError as refused:
        raise _run_error_envelope(refused) from refused
    session.commit()
    ran_long = now - as_utc(run.created_at) >= LONG_RUN_NOTIFICATION_AFTER
    return ok(
        data={
            **_run_payload(run),
            "confirmation": (
                f"'{registration.workprocess_name}' completed and its changes were applied."
            ),
        },
        meta={
            "affectedDataSourceKeys": _live_target_keys(registration),
            "ranLong": ran_long,
        },
    )


@router.post("/workprocesses/runs/{run_id}/cancel")
def cancel(run_id: uuid.UUID, session: _SessionDep, user_id: _UserDep) -> Envelope:
    """Leave = cancel (REQ-042): discard everything; nothing was applied.

    The only persistence is the run row flipping to ``discarded`` —
    retained as evidence, never deleted — with its pending answers still on
    it. No handler runs; there are no effects to unwind because none were
    ever made.
    """
    run, _registration = _own_run(session, run_id, user_id)
    try:
        cancel_run(session, run)
    except WorkprocessRunError as refused:
        raise _run_error_envelope(refused) from refused
    session.commit()
    return ok(data=_run_payload(run))


@router.get("/workprocesses/runs/{run_id}")
def get_run(run_id: uuid.UUID, session: _SessionDep, user_id: _UserDep) -> Envelope:
    """The caller's own run, wherever it stands — resume, review, or evidence."""
    run, _registration = _own_run(session, run_id, user_id)
    return ok(data=_run_payload(run))
