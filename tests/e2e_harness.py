"""The Playwright journeys' backing server — the REAL app over seeded data (WTK-200).

PI-010 demo posture: this harness boots the production application factory
(``mentorapp.main.create_app`` — every router, ``install_auth_wiring`` /
``install_home_wiring`` / ``install_records_wiring`` / ``install_panel_wiring``)
over a REAL SQLite database migrated to head through the same Alembic chain
production runs, then seeds a browsable mentoring world through the storage
layer. Only the CRM's HTTP edge is faked (:class:`FakeCrmTransport` behind
``get_espo_transport`` — the exact seam a deployment swaps), so login rides
the production Espo gateway → identity bridge → stored-session path, roles
are captured onto ``authSession.sessionRoleNames``, and the panel catalog,
grants, and REQ-019 row scoping all decide live. Email, conferencing,
transcripts, and drafting run on the sanctioned dev defaults the mentoring
router already binds (``LoggedEmailTransport`` + the deterministic fakes).

Run it::

    uv run uvicorn tests.e2e_harness:app --host 127.0.0.1 --port 8000
    cd frontend && npm run dev        # then open http://127.0.0.1:5173

Sign in as ``frank`` (any password) — mentor Frank Delgado: the Engagements
triage landing, the docked rollup preview, the /prep surface, accept/decline,
resources, events, and the seeded admin messages. ``janet`` (any password) is
the Leadership login and additionally sees "All Engagements" across mentors.

The database is ``tests/.e2e.db``, dropped and rebuilt from the migration
chain on every boot — seeding is idempotent by reconstruction. One thing is
still simulated at the edge because the journeys need it and the product
seams don't expose it: ``POST /e2e/crm/outage`` flips the fake CRM transport
into answering 503, the WTK-003 outage outcome. Session expiry is NOT
simulated any more: since FND-909 D9 every read/write resolves the acting
user from the session reference server-side, so ``POST /e2e/session/expire``
ages the stored ``authSession`` rows and the very next request rides the
production expiry path (``REAUTH_PENDING`` → the canonical ``reauthRequired``
refusal → in-place re-auth).
"""

from __future__ import annotations

import base64
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

from alembic import command
from alembic.config import Config
from pydantic import BaseModel
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session

from mentorapp.access import InMemoryLookupSources, LookupBinding
from mentorapp.api.deps import get_session
from mentorapp.api.envelope import Envelope, ok
from mentorapp.api.routers.records import get_lookup_sources
from mentorapp.api.wiring import get_espo_transport
from mentorapp.crm.espo import EspoResponse
from mentorapp.main import create_app
from mentorapp.storage import AdminMessage as AdminMessageRow
from mentorapp.storage import (
    AppUser,
    AuthSession,
    Client,
    CrmCompanyRef,
    CrmMentorRef,
    Engagement,
    Event,
    MentoringSession,
    OptionSet,
    OptionValue,
    Resource,
    SchemaRegistry,
    UserPreference,
    regenerate_read_views,
    utcnow,
)
from mentorapp.ui.navigation import NAVIGATION_PREFERENCE_KEY

_REPO_ROOT: Final = Path(__file__).resolve().parent.parent
_DB_PATH: Final = Path(__file__).with_name(".e2e.db")

# The production auth wiring reads these at request time (wiring.py's
# documented variables). Deterministic dev-only keys: the harness database is
# rebuilt every boot, so nothing sealed under them outlives the process run.
os.environ.setdefault(
    "MENTORAPP_CREDENTIAL_KEY",
    base64.urlsafe_b64encode(b"e2e-credential-key-32-bytes-....").decode(),
)
os.environ.setdefault(
    "MENTORAPP_TOKEN_SIGNING_KEY",
    base64.urlsafe_b64encode(b"e2e-action-link-signing-key").decode(),
)

MENTOR_LOGIN: Final = "frank"
LEADERSHIP_LOGIN: Final = "janet"
# Any password verifies (the fake CRM checks the login name only); the
# journeys still need SOMETHING to type.
DEMO_PASSWORD: Final = "mentor-demo"


@dataclass(frozen=True)
class CrmAccount:
    """One CRM-side staff account the fake Espo answers for."""

    crm_user_id: str
    display_name: str
    email: str
    teams: tuple[str, ...]


# Team names are the grant vocabulary verbatim (access/mentoring.py roles).
CRM_ACCOUNTS: Final[dict[str, CrmAccount]] = {
    MENTOR_LOGIN: CrmAccount("crm-frank", "Frank Delgado", "frank@cbm.org", ("Mentor",)),
    LEADERSHIP_LOGIN: CrmAccount("crm-janet", "Janet Osei", "janet@cbm.org", ("Leadership",)),
}


@dataclass
class FakeCrmTransport:
    """The one fake: EspoCRM's HTTP edge, with a runtime-flippable outage.

    Login (``GET App/user``) verifies the login NAME only — any password
    signs in — and answers Espo's real payload shape, so the production
    gateway/bridge/session path runs unmodified. ``crm_down`` answers 503,
    which the gateway maps to the distinct ``crmUnavailable`` refusal.
    """

    crm_down: bool = False

    def send(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> EspoResponse:
        if self.crm_down:
            return EspoResponse(503, None)
        if path == "App/user":
            try:
                raw = base64.b64decode(headers.get("Espo-Authorization", ""))
                login_name = raw.decode().split(":", 1)[0]
            except (ValueError, UnicodeDecodeError):
                return EspoResponse(401, None)
            account = CRM_ACCOUNTS.get(login_name)
            if account is None:
                return EspoResponse(401, None)
            return EspoResponse(
                200,
                {
                    "user": {
                        "id": account.crm_user_id,
                        "userName": login_name,
                        "name": account.display_name,
                        "emailAddress": account.email,
                        "teamsNames": {
                            str(index): team for index, team in enumerate(account.teams, 1)
                        },
                    },
                    "token": f"e2e-token-{login_name}",
                },
            )
        if path == "User/passwordChangeRequest":
            # Espo's own recovery flow "accepted the request" — nothing sends.
            return EspoResponse(200, {"requested": True})
        return EspoResponse(404, None)


@dataclass(frozen=True)
class SeededFacts:
    """The seeded identifiers a journey needs to address the app."""

    riverbend_engagement_id: uuid.UUID
    summit_engagement_id: uuid.UUID


def _fresh_migrated_engine() -> Engine:
    """Recreate tests/.e2e.db and upgrade it to head — the production chain.

    The same mechanism the migration tests use (tests/test_storage_migrations):
    the connection is handed to Alembic through ``config.attributes`` so the
    chain runs in place, then the generated read views (DB-S9) are built —
    startup work the migrations deliberately leave to the running app.
    """
    for suffix in ("", "-wal", "-shm"):
        Path(f"{_DB_PATH}{suffix}").unlink(missing_ok=True)
    engine = create_engine(
        f"sqlite+pysqlite:///{_DB_PATH}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _enable_fks(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]

    config = Config(_REPO_ROOT / "alembic.ini")
    config.set_main_option(
        "script_location", str(_REPO_ROOT / "src" / "mentorapp" / "storage" / "migrations")
    )
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    with Session(engine) as db:
        regenerate_read_views(db)
        db.commit()
    return engine


def _option_id(db: Session, set_name: str, value_name: str) -> uuid.UUID:
    """One seeded option value's id, addressed set-first (names repeat across sets)."""
    return db.scalars(
        select(OptionValue.option_value_id)
        .join(OptionSet, OptionSet.option_set_id == OptionValue.option_set_id)
        .where(OptionSet.option_set_name == set_name)
        .where(OptionValue.option_value_name == value_name)
        .where(OptionValue.deleted_at.is_(None))
    ).one()


def _engagement(
    db: Session,
    *,
    name: str,
    company_slug: str,
    status: str,
    mentor: CrmMentorRef | None,
    contact_name: str,
    contact_email: str,
    summary: str,
    program: str = "Core Mentoring",
    stage: str = "Growth",
) -> Engagement:
    """One company + client role + engagement, the REQ-086 subclass shape."""
    company = CrmCompanyRef(crm_company_id=company_slug)
    db.add(company)
    db.flush()
    client = Client(
        crm_company_ref_id=company.crm_company_ref_id,
        client_program=program,
        client_stage=stage,
    )
    db.add(client)
    db.flush()
    engagement = Engagement(
        engagement_name=name,
        engagement_status=_option_id(db, "engagementStatus", status),
        client_id=client.client_id,
        crm_mentor_ref_id=mentor.crm_mentor_ref_id if mentor is not None else None,
        engagement_summary=summary,
        primary_contact_name=contact_name,
        primary_contact_email=contact_email,
        primary_contact_crm_id=f"crm-contact-{company_slug}",
    )
    db.add(engagement)
    db.flush()
    return engagement


def _session_row(
    db: Session,
    engagement: Engagement,
    at: datetime,
    *,
    status: str,
    notes: str | None = None,
    action_items: str | None = None,
    link: str | None = None,
    external_meeting_id: str | None = None,
) -> MentoringSession:
    row = MentoringSession(
        engagement_id=engagement.engagement_id,
        scheduled_at=at,
        session_status=_option_id(db, "sessionStatus", status),
        session_notes=notes,
        action_items=action_items,
        conference_link=link,
        external_meeting_id=external_meeting_id,
    )
    db.add(row)
    db.flush()
    return row


def _seed(engine: Engine) -> SeededFacts:
    """Seed the PI-010 demo world through the storage layer (the API-test idiom).

    The migrated chain already carries the platform seeds (registry, option
    sets, the seven area sources + grants from 0014, the REQ-072 startup
    default from 0016); this adds the DOMAIN rows a human clicks through.
    Idempotent at boot by reconstruction: the database file is rebuilt before
    this runs, so re-running the harness always converges to the same world.
    """
    now = utcnow().replace(minute=0, second=0, microsecond=0)

    def days(offset: int, hour: int = 15) -> datetime:
        return (now + timedelta(days=offset)).replace(hour=hour)

    with Session(engine) as db:
        # --- Staff: the loginable mentor + leadership, and a second mentor
        # (data only) so leadership's "All Engagements" visibly spans mentors.
        frank = AppUser(crm_user_id="crm-frank", username=MENTOR_LOGIN)
        janet = AppUser(crm_user_id="crm-janet", username=LEADERSHIP_LOGIN)
        dana = AppUser(crm_user_id="crm-dana", username="dana")
        db.add_all([frank, janet, dana])
        db.flush()
        frank_anchor = CrmMentorRef(crm_mentor_id="crm-mentor-frank", user_id=frank.user_id)
        dana_anchor = CrmMentorRef(crm_mentor_id="crm-mentor-dana", user_id=dana.user_id)
        db.add_all([frank_anchor, dana_anchor])
        db.flush()

        # --- Frank's engagements: every REQ-075 status, ordered so the
        # triage ruling (pending first, imminent next, open action items,
        # name) is visible on the landing grid.
        riverbend = _engagement(
            db,
            name="Riverbend Bakery",
            company_slug="espo-riverbend-bakery",
            status="pendingAcceptance",
            mentor=frank_anchor,
            contact_name="Maria Kovac",
            contact_email="maria@riverbendbakery.com",
            summary=(
                "<p>Chagrin Falls bakery adding wholesale accounts; needs pricing "
                "and staffing guidance before signing two grocery contracts.</p>"
            ),
            stage="Intake",
        )

        summit = _engagement(
            db,
            name="Summit Auto Detail",
            company_slug="espo-summit-auto-detail",
            status="active",
            mentor=frank_anchor,
            contact_name="Deshawn Carter",
            contact_email="deshawn@summitautodetail.com",
            summary=(
                "<p>Family-run Akron detailing shop scaling from two bays to a "
                "mobile fleet; working the hiring plan and unit economics.</p>"
            ),
        )
        _session_row(
            db,
            summit,
            days(-70),
            status="completed",
            notes=(
                "<p>Kickoff. Walked the shop, mapped the service menu, and agreed "
                "the goal: a mobile unit running by fall without burning cash.</p>"
            ),
        )
        _session_row(
            db,
            summit,
            days(-56),
            status="completed",
            notes=(
                "<p>Reviewed twelve months of revenue by service. Ceramic coating "
                "carries the margin; basic washes lose money on Saturdays.</p>"
            ),
        )
        _session_row(
            db,
            summit,
            days(-42),
            status="completed",
            notes=(
                "<p>Priced the mobile package three ways against Medina and "
                "Fairlawn competitors; Deshawn favors the mid tier.</p>"
            ),
        )
        _session_row(
            db,
            summit,
            days(-28),
            status="completed",
            notes=(
                "<p>Worked the hiring math: a second detailer pays for himself at "
                "eleven mobile jobs a week. Drafted the interview loop.</p>"
            ),
            action_items=(
                "<ul><li>Send the pricing &amp; margin worksheet</li>"
                "<li>Deshawn drafts the detailer job posting</li></ul>"
            ),
        )
        _session_row(
            db,
            summit,
            days(-14),
            status="completed",
            notes=(
                "<p>Van lease vs. buy: leasing wins the first year on cash. "
                "Deshawn will get two insurance quotes before we commit.</p>"
            ),
            action_items=(
                "<ul><li>Collect two commercial auto insurance quotes</li>"
                "<li>Frank shares the cash-flow basics video</li></ul>"
            ),
            external_meeting_id="dev-meeting-summit-2026-06",
        )
        _session_row(
            db,
            summit,
            days(3),
            status="scheduled",
            link="https://conference.dev.invalid/m/summit-auto-detail-next",
            external_meeting_id="dev-meeting-summit-next",
        )

        cedar = _engagement(
            db,
            name="Cedar Point Consulting",
            company_slug="espo-cedar-point-consulting",
            status="active",
            mentor=frank_anchor,
            contact_name="Aisha Bell",
            contact_email="aisha@cedarpointconsulting.com",
            summary=(
                "<p>Sandusky HR consultancy; solid clients, no pipeline. Building "
                "a referral engine so revenue stops sawtoothing.</p>"
            ),
        )
        _session_row(
            db,
            cedar,
            days(-35),
            status="completed",
            notes="<p>Mapped the client list; 80% of revenue is three accounts.</p>",
        )
        _session_row(
            db,
            cedar,
            days(-9),
            status="completed",
            notes=(
                "<p>Drafted the referral ask script. Aisha will trial it with two "
                "warm contacts; nothing on the calendar yet — follow up.</p>"
            ),
            action_items=(
                "<ul><li>Aisha sends the referral ask to two warm contacts</li>"
                "<li>Schedule the next session once the trial runs</li></ul>"
            ),
        )

        lakewood = _engagement(
            db,
            name="Lakewood Yoga Studio",
            company_slug="espo-lakewood-yoga",
            status="onHold",
            mentor=frank_anchor,
            contact_name="Priya Raman",
            contact_email="priya@lakewoodyoga.com",
            summary=(
                "<p>On hold at the owner's request through the studio's summer "
                "renovation; resume in September.</p>"
            ),
            stage="Paused",
        )
        _session_row(
            db,
            lakewood,
            days(-50),
            status="completed",
            notes="<p>Membership pricing review; parked until the buildout finishes.</p>",
        )

        erie = _engagement(
            db,
            name="Erie Shore Charters",
            company_slug="espo-erie-shore-charters",
            status="dormant",
            mentor=frank_anchor,
            contact_name="Gus Lindqvist",
            contact_email="gus@erieshorecharters.com",
            summary=(
                "<p>Vermilion fishing charter; went quiet after the season "
                "started. Marked dormant until the client re-engages.</p>"
            ),
            stage="Dormant",
        )
        _session_row(
            db,
            erie,
            days(-120),
            status="completed",
            notes="<p>Seasonal cash plan sketched; no response to two follow-ups.</p>",
        )

        _engagement(
            db,
            name="Cuyahoga Valley Coffee Roasters",
            company_slug="espo-cuyahoga-valley-coffee",
            status="assigned",
            mentor=frank_anchor,
            contact_name="Tom Okafor",
            contact_email="tom@cvcoffeeroasters.com",
            summary=(
                "<p>Peninsula roastery, just accepted — intro email and first "
                "session still to go (the REQ-076 next steps).</p>"
            ),
            stage="Intake",
        )

        medina = _engagement(
            db,
            name="Medina Hardware Co.",
            company_slug="espo-medina-hardware",
            status="active",
            mentor=frank_anchor,
            contact_name="Ruth Vasquez",
            contact_email="ruth@medinahardware.com",
            summary=(
                "<p>Third-generation hardware store competing with the big boxes "
                "on service; building the contractor-accounts program.</p>"
            ),
        )
        _session_row(
            db,
            medina,
            days(-21),
            status="completed",
            notes=(
                "<p>Contractor account terms drafted; Ruth validates them with "
                "her top five pro customers before we price delivery.</p>"
            ),
        )
        # Scheduled with NO link yet — the /prep surface's paste-a-link path.
        _session_row(db, medina, days(10), status="scheduled")

        # --- Dana's engagement: invisible to frank, visible to leadership.
        portage = _engagement(
            db,
            name="Portage Trail Outfitters",
            company_slug="espo-portage-trail-outfitters",
            status="active",
            mentor=dana_anchor,
            contact_name="Beth Calloway",
            contact_email="beth@portagetrailoutfitters.com",
            summary="<p>Kent outdoor-gear retailer working an e-commerce launch.</p>",
        )
        _session_row(
            db,
            portage,
            days(-11),
            status="completed",
            notes="<p>Chose the storefront platform; inventory sync is next.</p>",
        )

        # --- The staff-maintained library (REQ-084) and events (REQ-085).
        for title, kind, location, description in (
            (
                "One-Page Business Plan Template",
                "document",
                "https://library.cbm.example/one-page-business-plan.docx",
                "The intake-session planning template most mentors start with.",
            ),
            (
                "Pricing & Margin Worksheet",
                "document",
                "https://library.cbm.example/pricing-margin-worksheet.xlsx",
                "Service-business pricing model with margin sensitivity tabs.",
            ),
            (
                "Cash-Flow Basics",
                "video",
                "https://videos.cbm.example/cash-flow-basics",
                "A 20-minute walkthrough for first cash-flow conversations.",
            ),
            (
                "SBA Cleveland District Office",
                "link",
                "https://www.sba.gov/district/cleveland",
                "Loan programs and local counseling referrals.",
            ),
        ):
            db.add(
                Resource(
                    resource_title=title,
                    resource_kind=_option_id(db, "resourceKind", kind),
                    resource_location=location,
                    resource_description=description,
                )
            )

        for title, offset, location, audience in (
            ("Mentor Roundtable — Independence Office", 7, "Independence, OH", "All mentors"),
            (
                "Client Marketing Workshop",
                21,
                "Tri-C Corporate College West",
                "Mentors & clients",
            ),
            ("CBM Annual Celebration", 45, "Cleveland Botanical Garden", "Everyone"),
        ):
            db.add(
                Event(
                    event_title=title,
                    starts_at=days(offset, hour=18),
                    event_location=location,
                    event_audience=audience,
                )
            )

        # --- Admin messages (REQ-011), posted by leadership: one urgent
        # requiring acknowledgment (banners across panels), one normal
        # requiring acknowledgment, one plain notice.
        for title, body, priority, requires_ack in (
            (
                "CRM maintenance Thursday night",
                "EspoCRM pauses Thursday 10 PM to midnight for maintenance. "
                "Notes saved in this app are unaffected.",
                "urgent",
                True,
            ),
            (
                "Updated mentoring guide",
                "The 2026 mentoring guide is posted in Resources — please review "
                "it before your next session.",
                "normal",
                True,
            ),
            (
                "Welcome to the new mentoring app",
                "Engagements, session notes, and session prep now live here. "
                "Your CRM login works as-is.",
                "normal",
                False,
            ),
        ):
            db.add(
                AdminMessageRow(
                    message_title=title,
                    message_body=body,
                    message_priority=priority,
                    requires_acknowledgment_flag=requires_ack,
                    created_by=janet.user_id,
                )
            )

        # --- Frank's navigation: one healthy pin and one pin whose view no
        # longer exists — the REQ-015 broken-pin journey's fixture.
        db.add(
            UserPreference(
                user_id=frank.user_id,
                preference_key=NAVIGATION_PREFERENCE_KEY,
                preference_value={
                    "presentation": "tabs",
                    "pins": [
                        {
                            "pinKey": "pin.myEngagements",
                            "panelKey": "engagements",
                            "viewKey": "mentorEngagements",
                            "label": "My Active Engagements",
                            "group": None,
                        },
                        {
                            "pinKey": "pin.q2Pipeline",
                            "panelKey": "engagements",
                            "viewKey": "views.q2PipelineReview",
                            "label": "Q2 Pipeline Review",
                            "group": None,
                        },
                    ],
                },
                created_by=frank.user_id,
                modified_by=frank.user_id,
            )
        )

        # --- Field settings the forms slice renders from (REL-004 block 1):
        # admin-maintained help text (REQ-040) and a duplicate-match rule
        # (REQ-037/059) — settings data, exactly where an admin would put it.
        summary_row = db.scalars(
            select(SchemaRegistry).where(
                SchemaRegistry.entity_type == "engagement",
                SchemaRegistry.field_name == "engagementSummary",
            )
        ).one()
        summary_row.help_text = (
            "What this engagement is about at a glance — shown on the triage "
            "preview and the prep surface."
        )
        name_row = db.scalars(
            select(SchemaRegistry).where(
                SchemaRegistry.entity_type == "engagement",
                SchemaRegistry.field_name == "engagementName",
            )
        ).one()
        name_row.validation_rules = {"duplicateMatchRules": ["byEngagementName"]}
        title_row = db.scalars(
            select(SchemaRegistry).where(
                SchemaRegistry.entity_type == "resource",
                SchemaRegistry.field_name == "resourceTitle",
            )
        ).one()
        title_row.validation_rules = {"duplicateMatchRules": ["byResourceTitle"]}

        db.commit()
        return SeededFacts(
            riverbend_engagement_id=riverbend.engagement_id,
            summit_engagement_id=summit.engagement_id,
        )


def _build_app() -> tuple[Any, Engine, FakeCrmTransport, SeededFacts]:
    engine = _fresh_migrated_engine()
    facts = _seed(engine)
    transport = FakeCrmTransport()

    def _request_session() -> Any:
        with Session(engine) as session:
            yield session

    application = create_app()
    # The overrides: the request DB session (onto the migrated, seeded
    # store), the CRM's HTTP edge, and the REQ-036 lookup bindings (which
    # have no durable store yet — REL-004 block 1 finding; the demo binds
    # each entity to its seeded area source). Everything else is the
    # production wiring create_app installed — including the D9 identity
    # seam, so every request REALLY resolves its acting user from the
    # session reference; the mentoring provider seams already default to
    # the sanctioned dev fakes.
    application.dependency_overrides[get_session] = _request_session
    application.dependency_overrides[get_espo_transport] = lambda: transport
    application.dependency_overrides[get_lookup_sources] = lambda: InMemoryLookupSources(
        [
            LookupBinding("client", "mentorClients"),
            LookupBinding("engagement", "mentorEngagements"),
            LookupBinding("session", "mentorSessions"),
            LookupBinding("resource", "mentorResources"),
            LookupBinding("event", "mentorEvents"),
        ]
    )
    return application, engine, transport, facts


app, _engine, _transport, _facts = _build_app()


class OutageBody(BaseModel):
    down: bool


@app.post("/e2e/session/expire")
def expire_session() -> Envelope:
    """Expire every ACTIVE session for real: age its absolute deadline.

    No middleware simulation (the pre-D9 posture): the next authenticated
    request runs the production path — ``SessionManagement.resolve`` finds
    the deadline passed, flips the record to ``REAUTH_PENDING``, and answers
    the canonical ``reauthRequired`` refusal the envelope client holds
    requests on; the journey's re-auth then revives the SAME session.
    """
    with Session(_engine) as db:
        rows = db.scalars(
            select(AuthSession).where(AuthSession.session_state == "active")
        ).all()
        for row in rows:
            row.session_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        return ok(data={"expired": len(rows)})


@app.post("/e2e/crm/outage")
def set_crm_outage(body: OutageBody) -> Envelope:
    """Flip the fake CRM's availability for the outage-messaging journey."""
    _transport.crm_down = body.down
    return ok(data={"down": body.down})


@app.get("/e2e/state")
def harness_state() -> Envelope:
    """The seeded facts a journey needs to address the app."""
    return ok(
        data={
            "loginName": MENTOR_LOGIN,
            "leadershipLoginName": LEADERSHIP_LOGIN,
            "password": DEMO_PASSWORD,
            "riverbendEngagementID": str(_facts.riverbend_engagement_id),
            "summitEngagementID": str(_facts.summit_engagement_id),
        }
    )
