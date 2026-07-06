"""WorkprocessExecutionFrame: the run engine behind REQ-042 (WTK-092).

The framework owns only the FRAME — launch, step order, branching, the two
exits — and this module is that frame over the storage rows
(:mod:`mentorapp.storage.workprocess`). Internal step behavior is the
workprocess author's freedom: the engine walks the registration's declared
step graph and stores answers; it never interprets what a step or an answer
means.

**The step graph** is the registration's ``stepGraph`` document::

    {"startStepKey": "chooseMentor",
     "steps": [{"stepKey": "chooseMentor", "nextStepKey": "confirm"},
               {"stepKey": "confirm",      "nextStepKey": null}]}

A step's ``nextStepKey`` is its default successor; ``null`` marks a terminal
step. Branching on earlier answers is the ANSWER's act: a step submission
may name any declared step as its ``nextStepKey`` override, and the engine
only checks the target is declared — which answers route where is authored
inside the steps, not in the framework. :func:`step_graph_problems` is the
one shape gate (wired at the API registration write): unique non-empty step
keys, a declared start, successors that exist, and at least one terminal
step so every graph CAN complete.

**Nothing commits until completion** (REQ-042): answers accumulate on the
run row's pending ``stepAnswers`` JSON — no application data is touched per
step. :func:`commit_run` is the one applying moment: it hands the frozen
:class:`WorkprocessCommitPayload` to the registration's OWN handler — the
:class:`WorkprocessCommitHandler` Protocol the app implements per
workprocess (the seam that keeps effects out of the framework) — on the
SAME session, then flips the run ``committed``; the caller's transaction
commits both together or neither (a raising handler leaves the run in
flight and nothing applied). :func:`cancel_run` is leave-=-cancel: one
state flip to ``discarded``; the row and its answers persist as evidence,
never deleted (the storage design's retention rule).

**Ran long → notification** (REQ-042): a commit whose run outlived
:data:`LONG_RUN_NOTIFICATION_AFTER` writes the standard REQ-014 bell entry
(type ``workprocessCompleted``) in the same transaction, so a user who
wandered off still learns the outcome; quick runs get only the API's
success confirmation.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Protocol

from sqlalchemy.orm import Session

from mentorapp.automation.worker import NOTIFICATION_RETENTION
from mentorapp.observability import get_logger
from mentorapp.storage import (
    RUN_STATE_COMMITTED,
    RUN_STATE_DISCARDED,
    RUN_STATE_IN_FLIGHT,
    Notification,
    WorkprocessRegistration,
    WorkprocessRun,
    as_utc,
    utcnow,
)

log = get_logger(__name__)

# A run past this age at commit "ran long" (REQ-042): the user has plausibly
# navigated away, so the confirmation alone may go unseen — the bell entry
# is the durable second channel. Frame configuration, not per-workprocess.
LONG_RUN_NOTIFICATION_AFTER: Final = timedelta(minutes=2)

# The stepGraph document's key vocabulary — named so the validator, the
# engine, and the API registration write all spell it identically.
GRAPH_START_KEY: Final = "startStepKey"
GRAPH_STEPS_KEY: Final = "steps"
STEP_KEY: Final = "stepKey"
STEP_NEXT_KEY: Final = "nextStepKey"


class WorkprocessRunError(Exception):
    """A run verb that the frame's rules refuse; maps to a 422 envelope.

    Every subclass carries what the educate-voice refusal needs to say —
    these are user mistakes to explain (wrong moment, wrong step), never
    access denials (those stay in :mod:`mentorapp.access.workprocess`).
    """


class RunNotInFlightError(WorkprocessRunError):
    """A step/commit/cancel arrived after the run already ended."""

    def __init__(self, run_state: str) -> None:
        self.run_state = run_state
        super().__init__(f"run is {run_state!r}, not in flight")


class NotCurrentStepError(WorkprocessRunError):
    """The answer names a step other than where the walk stands.

    The frame accepts answers only for the CURRENT step: branching means the
    path is decided by answers in order, so answering an arbitrary step
    would let a caller skip the very steps that route it.
    """

    def __init__(self, step_key: str, current_step_key: str | None) -> None:
        self.step_key = step_key
        self.current_step_key = current_step_key
        super().__init__(f"step {step_key!r} is not the current step {current_step_key!r}")


class UnknownBranchError(WorkprocessRunError):
    """The answer's ``nextStepKey`` override names no declared step."""

    def __init__(self, next_step_key: str) -> None:
        self.next_step_key = next_step_key
        super().__init__(f"nextStepKey {next_step_key!r} is not a declared step")


class RunNotCompletableError(WorkprocessRunError):
    """Commit arrived while the walk still stands on a step."""

    def __init__(self, current_step_key: str) -> None:
        self.current_step_key = current_step_key
        super().__init__(f"run still stands on step {current_step_key!r}")


def step_graph_problems(document: Any) -> list[str]:
    """Why this ``stepGraph`` document cannot be registered; empty = valid.

    The frame's ONE structural gate (REQ-041/REQ-042), returning every
    problem in one pass (the DB-S12 all-failures rule) as plain sentences
    the API wraps into field errors. Deliberately minimal — author freedom:
    no reachability or acyclicity judgment, only what the engine itself
    needs to walk (a declared start, resolvable successors) and what a run
    needs to ever finish (at least one terminal step).
    """
    if not isinstance(document, Mapping):
        return ["stepGraph must be an object with startStepKey and steps."]
    problems: list[str] = []
    steps = document.get(GRAPH_STEPS_KEY)
    if not isinstance(steps, list) or not steps:
        problems.append("steps must be a non-empty list of step declarations.")
        steps = []
    keys: list[str] = []
    for position, step in enumerate(steps):
        if not isinstance(step, Mapping) or not isinstance(step.get(STEP_KEY), str):
            problems.append(f"steps[{position}] must declare a string stepKey.")
            continue
        keys.append(step[STEP_KEY])
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        problems.append(f"step keys must be unique; duplicated: {duplicates}.")
    known = set(keys)
    for position, step in enumerate(steps):
        if not isinstance(step, Mapping):
            continue
        successor = step.get(STEP_NEXT_KEY)
        if successor is not None and successor not in known:
            problems.append(
                f"steps[{position}].nextStepKey {successor!r} names no declared step."
            )
    start = document.get(GRAPH_START_KEY)
    if start not in known:
        problems.append(f"startStepKey {start!r} must name a declared step.")
    if steps and not any(
        isinstance(step, Mapping) and step.get(STEP_NEXT_KEY) is None for step in steps
    ):
        problems.append("at least one step must be terminal (nextStepKey null).")
    return problems


def _declared_steps(registration: WorkprocessRegistration) -> dict[str, str | None]:
    """{stepKey: default successor} from the registration's graph document."""
    return {
        step[STEP_KEY]: step.get(STEP_NEXT_KEY)
        for step in registration.step_graph.get(GRAPH_STEPS_KEY, [])
        if isinstance(step, Mapping) and isinstance(step.get(STEP_KEY), str)
    }


@dataclass(frozen=True)
class WorkprocessCommitPayload:
    """Everything a workprocess's own commit handler gets to apply (REQ-042).

    The frame's whole knowledge of the run, frozen at the applying moment:
    which registration and run, the inherited selection (records + the
    launching source's key), the accumulated answers, and who ran it. What
    the payload MEANS is the handler's business — the seam is where author
    freedom leaves the framework.
    """

    workprocess_registration_id: uuid.UUID
    workprocess_name: str
    workprocess_run_id: uuid.UUID
    data_source_key: str
    selected_record_ids: tuple[str, ...]
    step_answers: Mapping[str, Any]
    user_id: uuid.UUID


class WorkprocessCommitHandler(Protocol):
    """The per-workprocess applying seam: the app implements one per registration.

    ``apply`` runs INSIDE the commit transaction, on the same session the
    run-state flip rides — the handler's writes and the ``committed`` state
    land together or not at all. Raising aborts the commit: the run stays in
    flight with its answers intact, and nothing applied.
    """

    def apply(self, session: Session, payload: WorkprocessCommitPayload) -> None:
        """Apply the completed run's effects. Raise to refuse the commit."""
        ...


class CommitHandlerRegistry(Protocol):
    """How the frame finds a registration's handler at commit time.

    ``None`` is a valid answer: an admin can register a workprocess without
    any framework code change (REQ-041), and a handler-less commit still
    completes — the run row IS the durable outcome, and a handler bound
    later serves future runs. Wiring binds the app's registry; tests pass
    in-memory ones.
    """

    def handler_for(self, workprocess_name: str) -> WorkprocessCommitHandler | None:
        """The handler for the named workprocess, or ``None`` when unbound."""
        ...


class InMemoryCommitHandlers:
    """Reference :class:`CommitHandlerRegistry` for tests and wiring defaults."""

    def __init__(self, handlers: dict[str, WorkprocessCommitHandler] | None = None) -> None:
        self._handlers = dict(handlers or {})

    def bind(self, workprocess_name: str, handler: WorkprocessCommitHandler) -> None:
        self._handlers[workprocess_name] = handler

    def handler_for(self, workprocess_name: str) -> WorkprocessCommitHandler | None:
        return self._handlers.get(workprocess_name)


def launch_run(
    session: Session,
    registration: WorkprocessRegistration,
    *,
    data_source_id: uuid.UUID,
    selected_record_ids: list[str],
    user_id: uuid.UUID,
) -> WorkprocessRun:
    """Open the frame: one in-flight run inheriting the selection (REQ-042).

    The walk starts on the graph's declared start step. Authorization
    (inherited data-source access) and selection-contract fit are the
    CALLER's gates — access in :mod:`mentorapp.access.workprocess`, the
    contract in the API's educate refusal via the grid standard's one
    explainer — so the engine assumes a sanctioned launch and only opens it.
    """
    run = WorkprocessRun(
        workprocess_registration_id=registration.workprocess_registration_id,
        data_source_id=data_source_id,
        user_id=user_id,
        selected_record_ids=list(selected_record_ids),
        current_step_key=registration.step_graph.get(GRAPH_START_KEY),
        created_by=user_id,
        modified_by=user_id,
    )
    session.add(run)
    session.flush()
    log.info(
        "workprocess run launched",
        extra={
            "context": {
                "workprocessRunID": str(run.workprocess_run_id),
                "workprocessRegistrationID": str(registration.workprocess_registration_id),
                "userID": str(user_id),
                "selectedCount": len(selected_record_ids),
            }
        },
    )
    return run


def _require_in_flight(run: WorkprocessRun) -> None:
    if run.run_state != RUN_STATE_IN_FLIGHT:
        raise RunNotInFlightError(run.run_state)


def answer_step(
    session: Session,
    run: WorkprocessRun,
    registration: WorkprocessRegistration,
    *,
    step_key: str,
    answer: Any,
    next_step_key: str | None = None,
) -> WorkprocessRun:
    """Record one step's answer and advance the walk (REQ-042).

    Only the CURRENT step answers (:class:`NotCurrentStepError` otherwise —
    order is what makes branching mean something). The successor is the
    answer's ``next_step_key`` override when given (any declared step —
    branching is the author's routing, the engine only checks it exists),
    else the step's declared default; a ``None`` successor completes the
    walk, making the run committable. The answer lands in the pending
    ``stepAnswers`` JSON — nothing else is touched (nothing commits until
    completion).
    """
    _require_in_flight(run)
    if run.current_step_key is None or step_key != run.current_step_key:
        raise NotCurrentStepError(step_key, run.current_step_key)
    successors = _declared_steps(registration)
    if next_step_key is not None:
        if next_step_key not in successors:
            raise UnknownBranchError(next_step_key)
        successor = next_step_key
    else:
        successor = successors.get(step_key)
    # Reassigned, never mutated in place: plain JSON columns only see whole-
    # value assignment, so an in-place update would silently not persist.
    run.step_answers = {**run.step_answers, step_key: answer}
    run.current_step_key = successor
    run.modified_by = run.user_id
    session.flush()
    return run


def cancel_run(session: Session, run: WorkprocessRun) -> WorkprocessRun:
    """Leave = cancel (REQ-042): flip to ``discarded``; nothing was ever applied.

    Discarding IS the whole act — the pending answers only ever lived on
    this row, so there are no effects to unwind. The row survives as
    evidence of the abandoned run (retained, never deleted), with
    ``completedAt`` stamping when it ended.
    """
    _require_in_flight(run)
    run.run_state = RUN_STATE_DISCARDED
    run.completed_at = utcnow()
    run.modified_by = run.user_id
    session.flush()
    log.info(
        "workprocess run discarded",
        extra={
            "context": {
                "workprocessRunID": str(run.workprocess_run_id),
                "answeredSteps": len(run.step_answers),
            }
        },
    )
    return run


def commit_run(
    session: Session,
    run: WorkprocessRun,
    registration: WorkprocessRegistration,
    *,
    data_source_key: str,
    handlers: CommitHandlerRegistry,
    now: datetime | None = None,
) -> WorkprocessCommitPayload:
    """The one applying moment (REQ-042): handler effects + the state flip, atomically.

    Requires a COMPLETED walk (the current step resolved to a terminal
    ``None``); an unfinished run refuses with :class:`RunNotCompletableError`
    rather than committing a partial answer set. The payload goes to the
    registration's own handler on THIS session — its writes and the
    ``committed`` flip share the caller's transaction, so a raising handler
    aborts both and the run stays in flight, answers intact. A run that
    outlived :data:`LONG_RUN_NOTIFICATION_AFTER` also writes the standard
    bell entry (REQ-014 shape) in the same transaction. Returns the payload
    so the API can confirm exactly what was applied.
    """
    _require_in_flight(run)
    if run.current_step_key is not None:
        raise RunNotCompletableError(run.current_step_key)
    moment = now if now is not None else utcnow()
    payload = WorkprocessCommitPayload(
        workprocess_registration_id=registration.workprocess_registration_id,
        workprocess_name=registration.workprocess_name,
        workprocess_run_id=run.workprocess_run_id,
        data_source_key=data_source_key,
        selected_record_ids=tuple(run.selected_record_ids),
        step_answers=dict(run.step_answers),
        user_id=run.user_id,
    )
    handler = handlers.handler_for(registration.workprocess_name)
    if handler is not None:
        handler.apply(session, payload)
    else:
        # Not an error (REQ-041: registrations need no framework code): the
        # run row is the durable outcome; a handler bound later serves
        # future runs. Logged so an unexpectedly effect-less commit is
        # diagnosable rather than silent.
        log.info(
            "workprocess commit has no bound handler",
            extra={"context": {"workprocessName": registration.workprocess_name}},
        )
    run.run_state = RUN_STATE_COMMITTED
    run.completed_at = moment
    run.modified_by = run.user_id
    if moment - as_utc(run.created_at) >= LONG_RUN_NOTIFICATION_AFTER:
        session.add(
            Notification(
                user_id=run.user_id,
                notification_type="workprocessCompleted",
                notification_message=(
                    f"'{registration.workprocess_name}' finished and its changes "
                    f"were applied. The grids it affects show the results."
                ),
                notification_expires_at=moment + NOTIFICATION_RETENTION,
            )
        )
    session.flush()
    log.info(
        "workprocess run committed",
        extra={
            "context": {
                "workprocessRunID": str(run.workprocess_run_id),
                "workprocessName": registration.workprocess_name,
                "answeredSteps": len(run.step_answers),
                "handlerBound": handler is not None,
            }
        },
    )
    return payload
