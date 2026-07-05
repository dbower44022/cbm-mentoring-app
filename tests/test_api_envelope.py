"""The envelope + structured-error contract (REQ-059): one shape, all failures
in one round trip, recovery bodies on 409, opaque logged 500s.
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mentorapp.access import (
    InMemorySessionStore,
    InMemoryTokenActionStore,
    SessionManagement,
    TokenActionService,
    VerifiedIdentity,
)
from mentorapp.access.grants import GrantLookup, InMemoryGrantRegistry
from mentorapp.api.deps import get_session
from mentorapp.api.envelope import Envelope, field_error, ok, request_error
from mentorapp.api.errors import (
    CODE_DUPLICATE_CANDIDATES,
    CODE_INTERNAL,
    CODE_NOT_FOUND,
    CODE_STALE_ROW_VERSION,
    ApiValidationError,
    DuplicateCandidatesError,
    RecordNotFoundError,
    StaleRowVersionError,
    register_error_handlers,
)
from mentorapp.api.routers.auth import (
    get_credential_verifier,
    get_forgot_password_flow,
    get_session_management,
    get_token_actions,
)
from mentorapp.api.routers.grids import get_grid_entity_catalog
from mentorapp.api.routers.home import get_home_catalog, get_message_center
from mentorapp.api.routers.records import get_record_catalog
from mentorapp.api.routers.shell import get_shell_catalog
from mentorapp.crm.auth import CredentialsRejectedError
from mentorapp.main import create_app
from mentorapp.ui.home_panel import MessageCenter
from mentorapp.ui.navigation import Panel, ViewRecord


def test_ok_envelope_shape() -> None:
    body = ok(data={"x": 1}, meta={"count": 1})
    assert body == {"data": {"x": 1}, "meta": {"count": 1}, "errors": None}
    assert ok() == {"data": None, "meta": {}, "errors": None}


def test_error_entry_shapes() -> None:
    assert field_error("mentorName", "required", "Name is required.") == {
        "fieldName": "mentorName",
        "code": "required",
        "message": "Name is required.",
    }
    assert request_error("notFound", "gone")["fieldName"] is None


class _CreateBody(BaseModel):
    mentor_name: str
    mentor_email: str


def _app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/boom/validation")
    def boom_validation() -> Envelope:
        raise ApiValidationError(
            [
                field_error("mentorName", "required", "Name is required."),
                field_error("mentorEmail", "invalidEmail", "Not a valid email."),
            ]
        )

    @app.post("/boom/create")
    def boom_create(body: _CreateBody) -> Envelope:
        return ok(data=body.model_dump())

    @app.patch("/boom/stale")
    def boom_stale() -> Envelope:
        raise StaleRowVersionError({"mentorID": "abc", "rowVersion": 7})

    @app.post("/boom/duplicate")
    def boom_duplicate() -> Envelope:
        raise DuplicateCandidatesError([{"mentorID": "abc"}, {"mentorID": "def"}])

    @app.get("/boom/missing")
    def boom_missing() -> Envelope:
        raise RecordNotFoundError("mentor", "abc")

    @app.get("/boom/unhandled")
    def boom_unhandled() -> Envelope:
        raise RuntimeError("secret internals")

    return app


def _client() -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


def test_validation_reports_all_failures_in_one_round_trip() -> None:
    resp = _client().post("/boom/validation")
    assert resp.status_code == 422
    body = resp.json()
    assert set(body) == {"data", "meta", "errors"}
    assert [e["fieldName"] for e in body["errors"]] == ["mentorName", "mentorEmail"]
    assert body["errors"][1]["code"] == "invalidEmail"


def test_request_validation_speaks_the_same_per_field_shape() -> None:
    resp = _client().post("/boom/create", json={})
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert {e["fieldName"] for e in errors} == {"mentor_name", "mentor_email"}
    assert all(e["code"] and e["message"] for e in errors)


def test_stale_row_version_is_409_with_current_record_in_data() -> None:
    resp = _client().patch("/boom/stale")
    assert resp.status_code == 409
    body = resp.json()
    assert body["data"] == {"mentorID": "abc", "rowVersion": 7}
    assert body["errors"][0]["code"] == CODE_STALE_ROW_VERSION


def test_duplicate_create_is_409_with_candidates_in_data() -> None:
    resp = _client().post("/boom/duplicate")
    assert resp.status_code == 409
    body = resp.json()
    assert [c["mentorID"] for c in body["data"]] == ["abc", "def"]
    assert body["errors"][0]["code"] == CODE_DUPLICATE_CANDIDATES


def test_not_found_is_404_in_the_envelope() -> None:
    resp = _client().get("/boom/missing")
    assert resp.status_code == 404
    body = resp.json()
    assert body["data"] is None
    assert body["errors"][0]["code"] == CODE_NOT_FOUND


def test_unhandled_exception_is_opaque_500_in_the_envelope() -> None:
    resp = _client().get("/boom/unhandled")
    assert resp.status_code == 500
    body = resp.json()
    assert body["errors"][0]["code"] == CODE_INTERNAL
    assert "secret internals" not in resp.text


# --- The mounted application: EVERY route speaks the envelope (WTK-145) -----
#
# The sweep is generated from the app's own OpenAPI document (routers are
# lazily wrapped in the route table, so the published contract is the reliable
# enumeration), meaning a future router cannot be mounted without inheriting
# these assertions — "every endpoint" is enforced mechanically, not by each
# endpoint suite remembering to check the shape. A route hidden with
# ``include_in_schema=False`` would escape; none exists, and hiding one is an
# off-contract act this suite should force a conversation about.

_HTTP_METHODS = frozenset({"get", "put", "post", "patch", "delete"})


def _probe_targets() -> list[tuple[str, str]]:
    paths = create_app().openapi()["paths"]
    return [
        (method.upper(), path)
        for path, operations in sorted(paths.items())
        for method in sorted(operations)
        if method in _HTTP_METHODS
    ]


class _SweepVerifier:
    """Refuse every credential pair — the sweep only needs a wired backend."""

    def verify(self, login_name: str, password: str) -> VerifiedIdentity:
        raise CredentialsRejectedError("sweep probe")


class _SweepForgotFlow:
    """Accept and drop every initiation — the sweep only needs a wired backend."""

    def initiate(self, login_name: str, email_address: str) -> None:
        return None


class _SweepCatalog:
    """An empty permissioned world — the sweep only needs a wired backend."""

    def accessible_panel_keys(self, user_id: uuid.UUID) -> tuple[str, ...]:
        return ()

    def available_view_keys(self, user_id: uuid.UUID) -> frozenset[str]:
        return frozenset()


class _SweepRecordCatalog:
    """Know no entity types — the sweep only needs a wired backend."""

    def entity_class(self, entity_type: str) -> type | None:
        return None


class _SweepGridCatalog:
    """Know no entity-backed data sources — the sweep only needs a wired backend."""

    def entity_for(self, data_source_key: str) -> tuple[str, type] | None:
        return None


class _SweepShellCatalog:
    """Know no panels, views, or grants — the sweep only needs a wired backend."""

    def panel(self, panel_key: str) -> Panel | None:
        return None

    def view(self, view_key: str) -> ViewRecord | None:
        return None

    def panels(self) -> tuple[Panel, ...]:
        return ()

    def views(self) -> tuple[ViewRecord, ...]:
        return ()

    def grants(self) -> GrantLookup:
        return InMemoryGrantRegistry()

    def user_roles(self, user_id: uuid.UUID) -> frozenset[str]:
        return frozenset()


@pytest.fixture()
def mounted_client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    # The auth backends are fail-loud until the PI-001 wiring lands (WTK-004);
    # like get_session above, the sweep binds test backends so a bare probe
    # exercises the mounted contract (a 4xx envelope), not the unwired 500.
    app.dependency_overrides[get_session_management] = lambda: SessionManagement(
        InMemorySessionStore()
    )
    app.dependency_overrides[get_token_actions] = lambda: TokenActionService(
        InMemoryTokenActionStore(), signing_key=b"sweep-probe"
    )
    app.dependency_overrides[get_credential_verifier] = _SweepVerifier
    app.dependency_overrides[get_forgot_password_flow] = _SweepForgotFlow
    # The home providers are fail-loud until their wiring lands (WTK-027);
    # same treatment as the auth seams above.
    app.dependency_overrides[get_home_catalog] = _SweepCatalog
    app.dependency_overrides[get_message_center] = MessageCenter
    # The record catalog is fail-loud until the domain entities wire it
    # (WTK-029); same treatment as the seams above.
    app.dependency_overrides[get_record_catalog] = _SweepRecordCatalog
    # The grid entity catalog is fail-loud until the grids wiring lands
    # (WTK-047); same treatment as the seams above.
    app.dependency_overrides[get_grid_entity_catalog] = _SweepGridCatalog
    # The shell catalog is fail-loud until the panel catalog wires it
    # (WTK-035); same treatment as the seams above.
    app.dependency_overrides[get_shell_catalog] = _SweepShellCatalog
    return TestClient(app, raise_server_exceptions=False)


def test_the_sweep_sees_the_known_surface() -> None:
    # Canary for the sweep itself: if route collection ever silently breaks,
    # this fails before the parametrized sweep vacuously passes.
    paths = {path for _, path in _probe_targets()}
    assert {"/healthz", "/schema/{entity_type}", "/preferences/{preference_key}"} <= paths


@pytest.mark.parametrize(
    ("method", "path_template"),
    [pytest.param(m, p, id=f"{m} {p}") for m, p in _probe_targets()],
)
def test_every_mounted_route_speaks_the_envelope(
    mounted_client: TestClient, method: str, path_template: str
) -> None:
    # A bare probe (no headers, no body, unknown path params) must come back as
    # a structured client response in the one envelope — never a 500, never a
    # bare FastAPI error shape.
    path = re.sub(r"\{[^}]+\}", "sweep-probe", path_template)
    resp = mounted_client.request(method, path)
    assert resp.status_code < 500
    body = resp.json()
    assert set(body) == {"data", "meta", "errors"}
    if resp.status_code < 400:
        assert body["errors"] is None
    else:
        assert body["errors"], "a failure must carry at least one structured error"
        for entry in body["errors"]:
            assert set(entry) == {"fieldName", "code", "message"}
            assert entry["code"]
            assert entry["message"]


def test_mounted_route_reports_failures_from_every_source_in_one_round_trip(
    mounted_client: TestClient,
) -> None:
    # One request, two failure sources (missing X-User-ID header, missing body
    # field): a real registered endpoint reports both together, proving the
    # one-round-trip rule holds on the mounted app, not just synthetic routes.
    resp = mounted_client.put("/preferences/grid.sweep.probe", json={})
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert {e["fieldName"] for e in errors} == {"X-User-ID", "preferenceValue"}
