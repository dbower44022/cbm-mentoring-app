"""The Playwright journeys' backing server (WTK-200).

Runs the REAL app factory over seeded local data with a stubbed CRM — the
testing standard's per-commit posture — by re-overriding the same provider
seams the API suites do (``tests/test_api_auth.py`` / ``test_api_shell.py``
own those fakes' reference shapes). Not a test module: uvicorn serves it for
``frontend/playwright.config.ts``::

    uv run uvicorn tests.e2e_harness:app --port 8000

Two things are simulated at the edge because the journeys need them and the
product seams don't expose them yet:

- ``POST /e2e/session/expire`` arms a middleware that answers every
  non-``/auth`` request with the canonical ``reauthRequired`` refusal until a
  re-auth succeeds — the read surfaces don't consume the sessionReference yet
  (the DEC-080 contract gap PI-011 closes), so expiry cannot be provoked
  through the API itself.
- ``POST /e2e/crm/outage`` flips the stub verifier into raising
  ``CrmUnavailableError``, the WTK-003 outage outcome.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool
from starlette.middleware.base import RequestResponseEndpoint

from mentorapp.access import (
    InMemorySessionStore,
    InMemoryTokenActionStore,
    SessionManagement,
    TokenActionService,
    VerifiedIdentity,
)
from mentorapp.access.grants import GrantLookup, InMemoryGrantRegistry, SourceGrant
from mentorapp.api.deps import get_session
from mentorapp.api.envelope import Envelope, ok
from mentorapp.api.errors import CODE_REAUTH_REQUIRED
from mentorapp.api.routers.auth import (
    get_credential_verifier,
    get_forgot_password_flow,
    get_session_management,
    get_token_actions,
)
from mentorapp.api.routers.home import get_home_catalog
from mentorapp.api.routers.records import get_record_catalog
from mentorapp.api.routers.shell import get_shell_catalog
from mentorapp.crm.auth import (
    CredentialsRejectedError,
    CrmUnavailableError,
    CrmUserCredential,
)
from mentorapp.main import create_app
from mentorapp.storage import Base, BaseEntity, UserPreference, entity_key, utcnow, uuid7
from mentorapp.ui.navigation import (
    HOME_PANEL,
    NAVIGATION_PREFERENCE_KEY,
    Panel,
    PanelType,
    ViewRecord,
)

MENTOR_LOGIN = "mentor@cbm.org"
MENTOR_PASSWORD = "correct-horse"


class E2EMentorRecord(BaseEntity):
    """One previewable entity so the record-window journey has a record."""

    __tablename__ = "E2eMentorRecord"

    e2e_mentor_id: Mapped[uuid.UUID] = entity_key("e2eMentorID")
    mentor_name: Mapped[str] = mapped_column("mentorName", nullable=False)


@dataclass
class StubVerifier:
    """CredentialVerifier double with a runtime-flippable outage."""

    accounts: dict[str, tuple[str, VerifiedIdentity]]
    crm_down: bool = False

    def verify(self, login_name: str, password: str) -> VerifiedIdentity:
        if self.crm_down:
            raise CrmUnavailableError("the CRM did not answer")
        entry = self.accounts.get(login_name)
        if entry is None or entry[0] != password:
            raise CredentialsRejectedError("the CRM refused the credentials")
        return entry[1]


@dataclass
class NoOpForgotPasswordFlow:
    """ForgotPasswordFlow double: the journeys never read recovery email."""

    initiated: list[tuple[str, str]] = field(default_factory=list)

    def initiate(self, login_name: str, email_address: str) -> None:
        self.initiated.append((login_name, email_address))


MENTORS_PANEL = Panel("mentors", "Mentors", PanelType.GRID, data_source_key="ds.mentors")
ACTIVE_VIEW = ViewRecord("views.activeMentors", "Active Mentors", "mentors")
REMOVED_VIEW = ViewRecord(
    "views.retiredMentors",
    "Retired Mentors",
    "mentors",
    deleted_at=utcnow(),
    deleted_by="Dana Admin",
)


@dataclass(frozen=True)
class StubShellCatalog:
    """Fixed panels/views plus a grant registry — the shell seam, stubbed."""

    registry: InMemoryGrantRegistry
    roles: frozenset[str] = frozenset({"mentor"})

    def panel(self, panel_key: str) -> Panel | None:
        return self._panels().get(panel_key)

    def view(self, view_key: str) -> ViewRecord | None:
        return self._views().get(view_key)

    def panels(self) -> tuple[Panel, ...]:
        return tuple(self._panels().values())

    def views(self) -> tuple[ViewRecord, ...]:
        return tuple(self._views().values())

    def grants(self) -> GrantLookup:
        return self.registry

    def user_roles(self, user_id: uuid.UUID) -> frozenset[str]:
        return self.roles

    @staticmethod
    def _panels() -> dict[str, Panel]:
        return {p.panel_key: p for p in (HOME_PANEL, MENTORS_PANEL)}

    @staticmethod
    def _views() -> dict[str, ViewRecord]:
        return {v.view_key: v for v in (ACTIVE_VIEW, REMOVED_VIEW)}


@dataclass(frozen=True)
class StubHomeCatalog:
    """The grant-derived Home world: what the seeded mentor can reach."""

    def accessible_panel_keys(self, user_id: uuid.UUID) -> tuple[str, ...]:
        return (HOME_PANEL.panel_key, MENTORS_PANEL.panel_key)

    def available_view_keys(self, user_id: uuid.UUID) -> frozenset[str]:
        return frozenset({ACTIVE_VIEW.view_key})


@dataclass(frozen=True)
class StubRecordCatalog:
    """One wire entity name for the preview/pop-out journey."""

    def entity_class(self, entity_type: str) -> type[Any] | None:
        return E2EMentorRecord if entity_type == "mentor" else None


@dataclass
class HarnessState:
    """Runtime switches the journeys flip through the /e2e endpoints."""

    session_expired: bool = False


def _mentor_identity() -> VerifiedIdentity:
    return VerifiedIdentity(
        user_id=uuid7(),
        role_names=frozenset({"mentor"}),
        crm_credential=CrmUserCredential(username="mentor.jane", secret="espo-token"),
    )


def _build_app() -> tuple[Any, HarnessState, StubVerifier, uuid.UUID]:
    # One static in-memory connection shared across request threads — the
    # conftest.py posture; the harness process is the whole database's life.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    mentor = _mentor_identity()
    with Session(engine) as seed:
        # The mentor's stored navigation: one healthy pin and one pin whose
        # view was soft-deleted — the broken-pin journey's fixture.
        seed.add(
            UserPreference(
                user_id=mentor.user_id,
                preference_key=NAVIGATION_PREFERENCE_KEY,
                preference_value={
                    "presentation": "tabs",
                    "pins": [
                        {
                            "pinKey": "pin.active",
                            "panelKey": ACTIVE_VIEW.panel_key,
                            "viewKey": ACTIVE_VIEW.view_key,
                            "label": ACTIVE_VIEW.name,
                            "group": None,
                        },
                        {
                            "pinKey": "pin.retired",
                            "panelKey": REMOVED_VIEW.panel_key,
                            "viewKey": REMOVED_VIEW.view_key,
                            "label": REMOVED_VIEW.name,
                            "group": None,
                        },
                    ],
                },
                created_by=mentor.user_id,
                modified_by=mentor.user_id,
            )
        )
        record = E2EMentorRecord(mentor_name="Ada Lovelace")
        seed.add(record)
        seed.flush()
        record_id = record.e2e_mentor_id
        seed.commit()

    state = HarnessState()
    verifier = StubVerifier(accounts={MENTOR_LOGIN: (MENTOR_PASSWORD, mentor)})
    sessions = SessionManagement(InMemorySessionStore(), now=utcnow)
    tokens = TokenActionService(
        InMemoryTokenActionStore(), signing_key=b"e2e-signing-key", now=utcnow
    )
    forgot = NoOpForgotPasswordFlow()
    registry = InMemoryGrantRegistry([SourceGrant("ds.mentors", "mentor")])

    def _request_session() -> Any:
        with Session(engine) as session:
            yield session

    application = create_app()
    application.dependency_overrides[get_session] = _request_session
    application.dependency_overrides[get_session_management] = lambda: sessions
    application.dependency_overrides[get_credential_verifier] = lambda: verifier
    application.dependency_overrides[get_token_actions] = lambda: tokens
    application.dependency_overrides[get_forgot_password_flow] = lambda: forgot
    application.dependency_overrides[get_shell_catalog] = lambda: StubShellCatalog(registry)
    application.dependency_overrides[get_home_catalog] = lambda: StubHomeCatalog()
    application.dependency_overrides[get_record_catalog] = lambda: StubRecordCatalog()
    return application, state, verifier, record_id


app, _state, _verifier, _record_id = _build_app()


class OutageBody(BaseModel):
    down: bool


@app.post("/e2e/session/expire")
def expire_session() -> Envelope:
    """Arm the expiry simulation: every non-/auth call refuses until re-auth."""
    _state.session_expired = True
    return ok(data={"expired": True})


@app.post("/e2e/crm/outage")
def set_crm_outage(body: OutageBody) -> Envelope:
    """Flip the stub CRM's availability for the outage-messaging journey."""
    _verifier.crm_down = body.down
    return ok(data={"down": body.down})


@app.get("/e2e/state")
def harness_state() -> Envelope:
    """The seeded facts a journey needs to address the app."""
    return ok(
        data={
            "recordId": str(_record_id),
            "loginName": MENTOR_LOGIN,
            "password": MENTOR_PASSWORD,
        }
    )


@app.middleware("http")
async def _simulated_expiry(request: Request, call_next: RequestResponseEndpoint) -> Response:
    # The auth surface stays live (re-auth must succeed) and the harness
    # switches stay reachable; everything else speaks the one refusal the
    # envelope client intercepts (mentorapp.api.errors.CODE_REAUTH_REQUIRED).
    path = request.url.path
    if _state.session_expired and not path.startswith(("/auth", "/e2e", "/healthz")):
        return JSONResponse(
            status_code=401,
            content={
                "data": None,
                "meta": {},
                "errors": [
                    {
                        "fieldName": None,
                        "code": CODE_REAUTH_REQUIRED,
                        "message": "The session expired; re-authenticate to continue.",
                    }
                ],
            },
        )
    response = await call_next(request)
    if path == "/auth/reauth" and response.status_code == 200:
        _state.session_expired = False
    return response
