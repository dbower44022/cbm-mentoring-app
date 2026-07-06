"""``/workprocesses`` endpoints (WTK-097, REQ-041/REQ-042) over the wire.

REQ-041: an admin registers a custom multi-step app as DATA (no framework
code change), the action appears in its target sources' lists for exactly
the users whose roles cover those sources, and registration verbs refuse
non-admins. REQ-042: the full run frame — launch inherits the selection,
steps branch on answers, nothing commits until completion (cancel discards,
handler failure aborts atomically), success confirms with the affected-grid
refresh list, and a long run writes the bell entry.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.access import CAP_WORKPROCESS_REGISTER, grant_data_source_role
from mentorapp.api.deps import get_session
from mentorapp.api.errors import CODE_CAPABILITY_REQUIRED, CODE_DATA_SOURCE_ACCESS_DENIED
from mentorapp.api.routers.workprocess import (
    CODE_DUPLICATE_WORKPROCESS_NAME,
    CODE_INVALID_STEP_GRAPH,
    CODE_NOT_CURRENT_STEP,
    CODE_RUN_NOT_COMPLETABLE,
    CODE_RUN_NOT_IN_FLIGHT,
    CODE_SELECTION_CONTRACT_VIOLATION,
    CODE_UNKNOWN_ACTION_CLASSIFICATION,
    CODE_UNKNOWN_BRANCH,
    CODE_UNKNOWN_DATA_SOURCE,
    CODE_UNKNOWN_SELECTION_CONTRACT,
    get_commit_handlers,
    get_role_source,
)
from mentorapp.automation.workprocess_engine import (
    InMemoryCommitHandlers,
    WorkprocessCommitPayload,
)
from mentorapp.main import create_app
from mentorapp.storage import (
    AccessGrant,
    AppUser,
    DataSource,
    Notification,
    WorkprocessRun,
    utcnow,
)

# Roles are session-scoped facts; the stub maps users to CRM-captured roles
# the way the wired role source will.
_MENTOR_ROLES = frozenset({"mentor"})


class _StubRoles:
    def __init__(self) -> None:
        self.roles: dict[uuid.UUID, frozenset[str]] = {}

    def user_roles(self, user_id: uuid.UUID) -> frozenset[str]:
        return self.roles.get(user_id, frozenset())


@pytest.fixture()
def roles() -> _StubRoles:
    return _StubRoles()


@pytest.fixture()
def handlers() -> InMemoryCommitHandlers:
    return InMemoryCommitHandlers()


@pytest.fixture()
def app_client(
    session: Session, roles: _StubRoles, handlers: InMemoryCommitHandlers
) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_role_source] = lambda: roles
    app.dependency_overrides[get_commit_handlers] = lambda: handlers
    return TestClient(app)


def _user(session: Session, username: str) -> uuid.UUID:
    user = AppUser(crm_user_id=f"crm-{username}", username=username)
    session.add(user)
    session.flush()
    return user.user_id


@pytest.fixture()
def admin_id(session: Session) -> uuid.UUID:
    admin = _user(session, "admin.one")
    session.add(AccessGrant(user_id=admin, access_grant_key=CAP_WORKPROCESS_REGISTER))
    session.flush()
    return admin


@pytest.fixture()
def mentor_id(session: Session, roles: _StubRoles) -> uuid.UUID:
    mentor = _user(session, "mentor.one")
    roles.roles[mentor] = _MENTOR_ROLES
    return mentor


@pytest.fixture()
def sources(session: Session) -> dict[str, DataSource]:
    by_key = {}
    for key in ("engagementRoster", "mentorRoster"):
        source = DataSource(
            data_source_key=key, data_source_name=key, data_source_sql="SELECT 1"
        )
        session.add(source)
        by_key[key] = source
    session.flush()
    # The mentor role covers the engagement roster only — the REQ-006 grant
    # every workprocess permission decision inherits from.
    grant_data_source_role(session, data_source_key="engagementRoster", role_name="mentor")
    session.commit()
    return by_key


def _headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-ID": str(user_id)}


def _branching_graph() -> dict[str, Any]:
    # chooseKind defaults to detailsA; an ANSWER may branch to detailsB.
    return {
        "startStepKey": "chooseKind",
        "steps": [
            {"stepKey": "chooseKind", "nextStepKey": "detailsA"},
            {"stepKey": "detailsA", "nextStepKey": "confirm"},
            {"stepKey": "detailsB", "nextStepKey": "confirm"},
            {"stepKey": "confirm", "nextStepKey": None},
        ],
    }


def _registration_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "workprocessName": "Bulk Reassign Mentor",
        "workprocessDescription": "Reassign selected engagements to another mentor",
        "targetDataSourceKeys": ["engagementRoster"],
        "selectionContract": "multiple",
        "actionClassification": "modifying",
        "stepGraph": _branching_graph(),
    }
    body.update(overrides)
    return body


def _register(app_client: TestClient, admin_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    response = app_client.post(
        "/workprocesses", json=_registration_body(**overrides), headers=_headers(admin_id)
    )
    assert response.status_code == 200, response.json()
    return response.json()["data"]


def _launch(
    app_client: TestClient,
    user_id: uuid.UUID,
    registration_id: str,
    *,
    data_source_key: str = "engagementRoster",
    selected: list[str] | None = None,
) -> dict[str, Any]:
    response = app_client.post(
        "/workprocesses/runs",
        json={
            "workprocessRegistrationID": registration_id,
            "dataSourceKey": data_source_key,
            "selectedRecordIDs": selected if selected is not None else ["rec-1", "rec-2"],
        },
        headers=_headers(user_id),
    )
    assert response.status_code == 200, response.json()
    return response.json()["data"]


def _step(
    app_client: TestClient,
    user_id: uuid.UUID,
    run_id: str,
    step_key: str,
    *,
    answer: Any = None,
    next_step_key: str | None = None,
) -> Any:
    body: dict[str, Any] = {"stepKey": step_key, "answer": answer}
    if next_step_key is not None:
        body["nextStepKey"] = next_step_key
    return app_client.post(
        f"/workprocesses/runs/{run_id}/step", json=body, headers=_headers(user_id)
    )


def _codes(response: Any) -> set[str]:
    return {error["code"] for error in response.json()["errors"]}


# --- Registration (REQ-041) ----------------------------------------------------------


def test_admin_registers_without_framework_code_changes(
    app_client: TestClient,
    admin_id: uuid.UUID,
    mentor_id: uuid.UUID,
    sources: dict[str, DataSource],
) -> None:
    # The whole REQ-041 loop as data: one POST, and the action is live in the
    # targeted source's list for users whose roles cover that source.
    created = _register(app_client, admin_id)
    assert created["targetDataSourceKeys"] == ["engagementRoster"]
    assert created["rowVersion"] == 1

    listed = app_client.get(
        "/workprocesses/actions/engagementRoster", headers=_headers(mentor_id)
    ).json()["data"]
    assert listed == [
        {
            "workprocessRegistrationID": created["workprocessRegistrationID"],
            "label": "Bulk Reassign Mentor",
            "description": "Reassign selected engagements to another mentor",
            "selectionContract": "multiple",
            "classification": "modifying",
        }
    ]


def test_registration_refuses_non_admin_with_the_capability_envelope(
    app_client: TestClient, mentor_id: uuid.UUID, sources: dict[str, DataSource]
) -> None:
    response = app_client.post(
        "/workprocesses", json=_registration_body(), headers=_headers(mentor_id)
    )
    assert response.status_code == 403
    assert _codes(response) == {CODE_CAPABILITY_REQUIRED}
    # Educate voice: names the capability so an admin knows what to grant.
    assert CAP_WORKPROCESS_REGISTER in response.json()["errors"][0]["message"]
    # The management list is gated by the same capability.
    assert app_client.get("/workprocesses", headers=_headers(mentor_id)).status_code == 403


def test_registration_reports_every_failure_in_one_round_trip(
    app_client: TestClient, admin_id: uuid.UUID, sources: dict[str, DataSource]
) -> None:
    response = app_client.post(
        "/workprocesses",
        json=_registration_body(
            selectionContract="some",
            actionClassification="bulk",
            targetDataSourceKeys=["engagementRoster", "noSuchSource"],
            stepGraph={
                "startStepKey": "ghost",
                "steps": [{"stepKey": "confirm", "nextStepKey": "ghost"}],
            },
        ),
        headers=_headers(admin_id),
    )
    assert response.status_code == 422
    assert _codes(response) == {
        CODE_UNKNOWN_SELECTION_CONTRACT,
        CODE_UNKNOWN_ACTION_CLASSIFICATION,
        CODE_UNKNOWN_DATA_SOURCE,
        CODE_INVALID_STEP_GRAPH,
    }


def test_duplicate_registration_name_refused(
    app_client: TestClient, admin_id: uuid.UUID, sources: dict[str, DataSource]
) -> None:
    _register(app_client, admin_id)
    response = app_client.post(
        "/workprocesses", json=_registration_body(), headers=_headers(admin_id)
    )
    assert response.status_code == 422
    assert _codes(response) == {CODE_DUPLICATE_WORKPROCESS_NAME}


def test_patch_retargets_and_stale_row_version_conflicts(
    app_client: TestClient,
    admin_id: uuid.UUID,
    mentor_id: uuid.UUID,
    sources: dict[str, DataSource],
) -> None:
    created = _register(app_client, admin_id)
    registration_id = created["workprocessRegistrationID"]

    patched = app_client.patch(
        f"/workprocesses/{registration_id}",
        json={"rowVersion": 1, "targetDataSourceKeys": ["mentorRoster"]},
        headers=_headers(admin_id),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["targetDataSourceKeys"] == ["mentorRoster"]
    # Retargeting moved the action list: the engagement roster no longer
    # carries the action (restriction by tighter sources, never hiding).
    assert (
        app_client.get(
            "/workprocesses/actions/engagementRoster", headers=_headers(mentor_id)
        ).json()["data"]
        == []
    )

    stale = app_client.patch(
        f"/workprocesses/{registration_id}",
        json={"rowVersion": 1, "workprocessName": "Renamed"},
        headers=_headers(admin_id),
    )
    # DB-S4: the current record rides the 409 for merge/refresh.
    assert stale.status_code == 409
    assert stale.json()["data"]["rowVersion"] == 2


def test_delete_retires_the_action_from_every_target_list(
    app_client: TestClient,
    admin_id: uuid.UUID,
    mentor_id: uuid.UUID,
    sources: dict[str, DataSource],
) -> None:
    created = _register(app_client, admin_id)
    registration_id = created["workprocessRegistrationID"]

    deleted = app_client.delete(f"/workprocesses/{registration_id}", headers=_headers(admin_id))
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {
        "workprocessRegistrationID": registration_id,
        "deleted": True,
        "targetsRetired": 1,
    }
    assert (
        app_client.get(
            "/workprocesses/actions/engagementRoster", headers=_headers(mentor_id)
        ).json()["data"]
        == []
    )
    # And the launch path closed with it (uniform 404 — soft-deleted and
    # never-existed answer identically).
    response = app_client.post(
        "/workprocesses/runs",
        json={
            "workprocessRegistrationID": registration_id,
            "dataSourceKey": "engagementRoster",
            "selectedRecordIDs": ["rec-1"],
        },
        headers=_headers(mentor_id),
    )
    assert response.status_code == 404


# --- The action list inherits data-source access (REQ-041) ---------------------------


def test_action_list_is_inherited_never_per_user_trimmed(
    app_client: TestClient,
    admin_id: uuid.UUID,
    mentor_id: uuid.UUID,
    sources: dict[str, DataSource],
    session: Session,
    roles: _StubRoles,
) -> None:
    _register(app_client, admin_id)
    outsider = _user(session, "outsider.one")
    roles.roles[outsider] = frozenset({"finance"})
    session.commit()

    # Covered role: the source's whole list. Uncovered role: empty — and an
    # UNKNOWN source answers identically, so source keys cannot be probed.
    assert (
        len(
            app_client.get(
                "/workprocesses/actions/engagementRoster", headers=_headers(mentor_id)
            ).json()["data"]
        )
        == 1
    )
    for key in ("engagementRoster", "noSuchSource"):
        response = app_client.get(f"/workprocesses/actions/{key}", headers=_headers(outsider))
        assert response.status_code == 200
        assert response.json()["data"] == []


# --- The run frame (REQ-042) ----------------------------------------------------------


def test_full_lifecycle_launch_steps_branch_commit(
    app_client: TestClient,
    admin_id: uuid.UUID,
    mentor_id: uuid.UUID,
    sources: dict[str, DataSource],
    session: Session,
    handlers: InMemoryCommitHandlers,
) -> None:
    applied: list[WorkprocessCommitPayload] = []

    class _Handler:
        def apply(self, handler_session: Session, payload: WorkprocessCommitPayload) -> None:
            applied.append(payload)

    handlers.bind("Bulk Reassign Mentor", _Handler())
    created = _register(app_client, admin_id)

    # Launch inherits the selection and opens on the declared start step.
    run = _launch(app_client, mentor_id, created["workprocessRegistrationID"])
    assert run["runState"] == "inFlight"
    assert run["currentStepKey"] == "chooseKind"
    assert run["selectedRecordIDs"] == ["rec-1", "rec-2"]
    assert run["completable"] is False
    run_id = run["workprocessRunID"]

    # The first answer BRANCHES: it overrides the declared detailsA default
    # and routes to detailsB — branching is the answer's act (REQ-042).
    branched = _step(
        app_client,
        mentor_id,
        run_id,
        "chooseKind",
        answer={"kind": "B"},
        next_step_key="detailsB",
    )
    assert branched.json()["data"]["currentStepKey"] == "detailsB"

    _step(app_client, mentor_id, run_id, "detailsB", answer={"mentorID": "m-9"})
    final = _step(app_client, mentor_id, run_id, "confirm", answer=True)
    assert final.json()["data"]["currentStepKey"] is None
    assert final.json()["data"]["completable"] is True

    # Nothing committed yet: the handler has seen nothing.
    assert applied == []

    committed = app_client.post(
        f"/workprocesses/runs/{run_id}/commit", headers=_headers(mentor_id)
    )
    assert committed.status_code == 200
    body = committed.json()
    assert body["data"]["runState"] == "committed"
    assert "Bulk Reassign Mentor" in body["data"]["confirmation"]
    # The affected grids to refresh, and this quick run rang no bell.
    assert body["meta"]["affectedDataSourceKeys"] == ["engagementRoster"]
    assert body["meta"]["ranLong"] is False

    # The handler got the frame's whole knowledge of the run, exactly once.
    payload = applied[0]
    assert payload.workprocess_name == "Bulk Reassign Mentor"
    assert payload.data_source_key == "engagementRoster"
    assert payload.selected_record_ids == ("rec-1", "rec-2")
    assert payload.step_answers == {
        "chooseKind": {"kind": "B"},
        "detailsB": {"mentorID": "m-9"},
        "confirm": True,
    }
    assert payload.user_id == mentor_id

    stored = session.scalars(select(WorkprocessRun)).one()
    assert stored.run_state == "committed"
    assert stored.completed_at is not None
    # A quick run writes no bell entry (REQ-042: notify only when it ran long).
    assert session.scalars(select(Notification)).all() == []


def test_cancel_discards_everything_except_the_evidence_row(
    app_client: TestClient,
    admin_id: uuid.UUID,
    mentor_id: uuid.UUID,
    sources: dict[str, DataSource],
    session: Session,
) -> None:
    created = _register(app_client, admin_id)
    run = _launch(app_client, mentor_id, created["workprocessRegistrationID"])
    run_id = run["workprocessRunID"]
    _step(app_client, mentor_id, run_id, "chooseKind", answer={"kind": "A"})

    cancelled = app_client.post(
        f"/workprocesses/runs/{run_id}/cancel", headers=_headers(mentor_id)
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["runState"] == "discarded"

    # Evidence, not deletion: the row survives with its pending answers.
    stored = session.scalars(select(WorkprocessRun)).one()
    assert stored.deleted_at is None
    assert stored.step_answers == {"chooseKind": {"kind": "A"}}
    assert stored.completed_at is not None

    # The frame is closed: every further verb refuses in the educate voice.
    reopened = _step(app_client, mentor_id, run_id, "detailsA", answer={})
    assert reopened.status_code == 422
    assert _codes(reopened) == {CODE_RUN_NOT_IN_FLIGHT}


def test_commit_refuses_an_unfinished_run(
    app_client: TestClient,
    admin_id: uuid.UUID,
    mentor_id: uuid.UUID,
    sources: dict[str, DataSource],
) -> None:
    created = _register(app_client, admin_id)
    run = _launch(app_client, mentor_id, created["workprocessRegistrationID"])

    response = app_client.post(
        f"/workprocesses/runs/{run['workprocessRunID']}/commit", headers=_headers(mentor_id)
    )
    assert response.status_code == 422
    assert _codes(response) == {CODE_RUN_NOT_COMPLETABLE}
    # Educate voice: says where the run stands and that nothing applies yet.
    assert "chooseKind" in response.json()["errors"][0]["message"]


def test_step_order_and_branch_targets_are_enforced(
    app_client: TestClient,
    admin_id: uuid.UUID,
    mentor_id: uuid.UUID,
    sources: dict[str, DataSource],
) -> None:
    created = _register(app_client, admin_id)
    run = _launch(app_client, mentor_id, created["workprocessRegistrationID"])
    run_id = run["workprocessRunID"]

    # Answering a step the walk does not stand on refuses — order is what
    # makes branching mean something.
    out_of_order = _step(app_client, mentor_id, run_id, "confirm", answer=True)
    assert out_of_order.status_code == 422
    assert _codes(out_of_order) == {CODE_NOT_CURRENT_STEP}

    # Branching only routes to DECLARED steps.
    bad_branch = _step(
        app_client, mentor_id, run_id, "chooseKind", answer={}, next_step_key="ghost"
    )
    assert bad_branch.status_code == 422
    assert _codes(bad_branch) == {CODE_UNKNOWN_BRANCH}


def test_selection_contract_violation_educates_with_the_grid_voice(
    app_client: TestClient,
    admin_id: uuid.UUID,
    mentor_id: uuid.UUID,
    sources: dict[str, DataSource],
) -> None:
    created = _register(
        app_client, admin_id, workprocessName="Open Engagement", selectionContract="single"
    )
    response = app_client.post(
        "/workprocesses/runs",
        json={
            "workprocessRegistrationID": created["workprocessRegistrationID"],
            "dataSourceKey": "engagementRoster",
            "selectedRecordIDs": ["rec-1", "rec-2", "rec-3"],
        },
        headers=_headers(mentor_id),
    )
    assert response.status_code == 422
    error = response.json()["errors"][0]
    assert error["code"] == CODE_SELECTION_CONTRACT_VIOLATION
    assert error["fieldName"] == "selectedRecordIDs"
    # The grid standard's one explainer: what happened, why, what next —
    # naming the action and the actual selection.
    assert "Open Engagement" in error["message"]
    assert "exactly one" in error["message"]


def test_launch_refusals_permission_and_untargeted(
    app_client: TestClient,
    admin_id: uuid.UUID,
    mentor_id: uuid.UUID,
    sources: dict[str, DataSource],
    session: Session,
    roles: _StubRoles,
) -> None:
    created = _register(app_client, admin_id)
    outsider = _user(session, "outsider.one")
    roles.roles[outsider] = frozenset({"finance"})
    session.commit()

    # No role covering the source: the standard REQ-006 403 — inherited,
    # not workprocess-specific.
    denied = app_client.post(
        "/workprocesses/runs",
        json={
            "workprocessRegistrationID": created["workprocessRegistrationID"],
            "dataSourceKey": "engagementRoster",
            "selectedRecordIDs": ["rec-1"],
        },
        headers=_headers(outsider),
    )
    assert denied.status_code == 403
    assert _codes(denied) == {CODE_DATA_SOURCE_ACCESS_DENIED}

    # A source the registration does not target answers the uniform 404,
    # even for a user who can access that source elsewhere.
    grant_data_source_role(session, data_source_key="mentorRoster", role_name="mentor")
    session.commit()
    untargeted = app_client.post(
        "/workprocesses/runs",
        json={
            "workprocessRegistrationID": created["workprocessRegistrationID"],
            "dataSourceKey": "mentorRoster",
            "selectedRecordIDs": ["rec-1"],
        },
        headers=_headers(mentor_id),
    )
    assert untargeted.status_code == 404


def test_runs_are_the_launching_users_own_frame(
    app_client: TestClient,
    admin_id: uuid.UUID,
    mentor_id: uuid.UUID,
    sources: dict[str, DataSource],
    session: Session,
    roles: _StubRoles,
) -> None:
    created = _register(app_client, admin_id)
    run = _launch(app_client, mentor_id, created["workprocessRegistrationID"])
    run_id = run["workprocessRunID"]

    other = _user(session, "mentor.two")
    roles.roles[other] = _MENTOR_ROLES
    session.commit()

    # Another user's run answers exactly like one that never existed — on
    # the read AND on every verb.
    assert (
        app_client.get(f"/workprocesses/runs/{run_id}", headers=_headers(other)).status_code
        == 404
    )
    assert _step(app_client, other, run_id, "chooseKind", answer={}).status_code == 404
    # The owner still stands where they stood.
    own = app_client.get(f"/workprocesses/runs/{run_id}", headers=_headers(mentor_id))
    assert own.json()["data"]["currentStepKey"] == "chooseKind"


def test_handler_failure_aborts_the_commit_atomically(
    session: Session,
    roles: _StubRoles,
    handlers: InMemoryCommitHandlers,
    admin_id: uuid.UUID,
    mentor_id: uuid.UUID,
    sources: dict[str, DataSource],
) -> None:
    class _Refusing:
        def apply(self, handler_session: Session, payload: WorkprocessCommitPayload) -> None:
            raise RuntimeError("downstream system said no")

    handlers.bind("Bulk Reassign Mentor", _Refusing())
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_role_source] = lambda: roles
    app.dependency_overrides[get_commit_handlers] = lambda: handlers
    # raise_server_exceptions=False: the opaque-500 path is the behavior
    # under test, not an accident to re-raise.
    client = TestClient(app, raise_server_exceptions=False)

    created = _register(client, admin_id)
    run = _launch(client, mentor_id, created["workprocessRegistrationID"])
    run_id = run["workprocessRunID"]
    _step(client, mentor_id, run_id, "chooseKind", answer={})
    _step(client, mentor_id, run_id, "detailsA", answer={})
    _step(client, mentor_id, run_id, "confirm", answer=True)

    response = client.post(f"/workprocesses/runs/{run_id}/commit", headers=_headers(mentor_id))
    assert response.status_code == 500

    # Atomic: the handler raised, so the state flip never landed — the run
    # is still in flight with its answers intact, and nothing was applied.
    session.rollback()
    stored = session.scalars(select(WorkprocessRun)).one()
    assert stored.run_state == "inFlight"
    assert stored.step_answers["confirm"] is True


def test_long_run_commit_rings_the_bell(
    app_client: TestClient,
    admin_id: uuid.UUID,
    mentor_id: uuid.UUID,
    sources: dict[str, DataSource],
    session: Session,
) -> None:
    created = _register(app_client, admin_id)
    run = _launch(app_client, mentor_id, created["workprocessRegistrationID"])
    run_id = run["workprocessRunID"]
    _step(app_client, mentor_id, run_id, "chooseKind", answer={})
    _step(app_client, mentor_id, run_id, "detailsA", answer={})
    _step(app_client, mentor_id, run_id, "confirm", answer=True)

    # Backdate the launch so the commit sees a long-lived run (REQ-042:
    # "notification if it ran long") without the test actually waiting.
    stored = session.scalars(select(WorkprocessRun)).one()
    stored.created_at = utcnow() - timedelta(minutes=5)
    session.commit()

    committed = app_client.post(
        f"/workprocesses/runs/{run_id}/commit", headers=_headers(mentor_id)
    )
    assert committed.status_code == 200
    assert committed.json()["meta"]["ranLong"] is True

    bell = session.scalars(select(Notification)).one()
    assert bell.user_id == mentor_id
    assert bell.notification_type == "workprocessCompleted"
    assert "Bulk Reassign Mentor" in bell.notification_message
