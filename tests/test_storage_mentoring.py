"""PI-010 domain entities: subclasses, associations, vocabularies (WTK-185).

Covers the storage half of the domain data foundation: the REQ-086 company
subclass model, the engagement with its REQ-075 status vocabulary, the
session's REQ-074/079/082 fields and WTK-182 transcript discipline, and the
REQ-084/085 resource and event entities.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mentorapp.storage import (
    ENGAGEMENT_STATUS_OPTION_SET,
    ENGAGEMENT_STATUS_VALUES,
    OWNERSHIP_SIDES,
    RESOURCE_KIND_VALUES,
    SESSION_STATUS_VALUES,
    Base,
    BaseEntity,
    Client,
    CrmCompanyRef,
    CrmMentorRef,
    Engagement,
    Event,
    MentoringSession,
    OptionSet,
    OptionValue,
    Partner,
    ProgressGoal,
    Resource,
    built_in_fields,
    seed_built_in_registry,
)

_DOMAIN_ENTITIES = (Client, Partner, Engagement, MentoringSession, Resource, Event)


def _company(session: Session, crm_id: str = "acct-1") -> CrmCompanyRef:
    company = CrmCompanyRef(crm_company_id=crm_id)
    session.add(company)
    session.flush()
    return company


def _engagement(session: Session, name: str = "Acme mentoring") -> Engagement:
    company = _company(session, crm_id=f"acct-{name}")
    client = Client(crm_company_ref_id=company.crm_company_ref_id)
    session.add(client)
    session.flush()
    engagement = Engagement(engagement_name=name, client_id=client.client_id)
    session.add(engagement)
    session.flush()
    return engagement


# --- Ownership sides (REQ-062/REQ-063 carried forward) ------------------------------


def test_every_entity_declares_its_ownership_side() -> None:
    # The mentoring domain is application-owned working data; the anchors
    # declare the CRM side. ProgressGoal continues PI-009's declaration.
    for entity in (*_DOMAIN_ENTITIES, ProgressGoal):
        assert entity.__ownership_side__ == "application"
    for entity in (CrmCompanyRef, CrmMentorRef):
        assert entity.__ownership_side__ == "crm"
    for mapper in BaseEntity.registry.mappers:
        if issubclass(mapper.class_, BaseEntity):
            assert mapper.class_.__ownership_side__ in OWNERSHIP_SIDES


# --- REQ-086: the company subclass model --------------------------------------------


def test_client_and_partner_are_roles_of_one_company_record(session: Session) -> None:
    # ONE organization record; the roles are subclass rows on its anchor —
    # a company that is both client and partner has one anchor, two roles.
    company = _company(session)
    session.add(
        Client(
            crm_company_ref_id=company.crm_company_ref_id,
            client_since=date(2026, 1, 15),
            client_program="Core mentoring",
            client_referral_source="SBA workshop",
            client_stage="Growth",
        )
    )
    session.add(Partner(crm_company_ref_id=company.crm_company_ref_id))
    session.commit()

    client = session.scalars(select(Client)).one()
    partner = session.scalars(select(Partner)).one()
    assert client.crm_company_ref.crm_company_id == "acct-1"
    assert partner.crm_company_ref.crm_company_id == "acct-1"
    assert client.client_since == date(2026, 1, 15)


@pytest.mark.parametrize("role_entity", [Client, Partner])
def test_at_most_one_live_role_row_per_company(
    session: Session, role_entity: type[Client] | type[Partner]
) -> None:
    # REQ-086's "no duplicate company rows per role", enforced as a partial
    # 1:1: a second live role row collides; a soft-deleted one never blocks
    # re-declaring the role later (REQ-052).
    company = _company(session)
    session.add(role_entity(crm_company_ref_id=company.crm_company_ref_id))
    session.commit()

    session.add(role_entity(crm_company_ref_id=company.crm_company_ref_id))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    live = session.scalars(select(role_entity)).one()
    live.soft_delete()
    session.commit()
    session.add(role_entity(crm_company_ref_id=company.crm_company_ref_id))
    session.commit()


def test_role_rows_require_a_real_company_anchor(session: Session) -> None:
    session.add(Client(crm_company_ref_id=uuid.uuid4()))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --- REQ-075: the engagement and its status vocabulary ------------------------------


def test_engagement_status_vocabulary_is_option_set_data(session: Session) -> None:
    # The six stakeholder-confirmed statuses ride the DB-S7 option-set
    # machinery: seeded as data in declaration order, never a DB enum.
    seed_built_in_registry(session, [Engagement])
    option_set = session.scalars(
        select(OptionSet).where(OptionSet.option_set_name == ENGAGEMENT_STATUS_OPTION_SET)
    ).one()
    values = session.scalars(
        select(OptionValue)
        .where(OptionValue.option_set_id == option_set.option_set_id)
        .order_by(OptionValue.option_value_sort_order)
    ).all()
    assert [(v.option_value_name, v.option_value_label) for v in values] == list(
        ENGAGEMENT_STATUS_VALUES
    )
    assert [label for _, label in ENGAGEMENT_STATUS_VALUES] == [
        "Active",
        "Pending Acceptance",
        "Assigned",
        "On Hold",
        "Dormant",
        "Assignment Declined",
    ]


def test_engagement_links_client_mentor_and_optional_crm_anchor(session: Session) -> None:
    engagement = _engagement(session)
    mentor = CrmMentorRef(crm_mentor_id="mentor-1")
    session.add(mentor)
    session.flush()
    engagement.crm_mentor_ref_id = mentor.crm_mentor_ref_id
    engagement.primary_contact_name = "Dana Ortiz"
    engagement.primary_contact_email = "dana@acme.example"
    session.commit()

    fetched = session.scalars(select(Engagement)).one()
    assert fetched.engagement_id.version == 7
    assert fetched.client is not None
    assert fetched.crm_mentor_ref is not None
    # App-born engagements need no CRM anchor (the working home is here);
    # the anchor stays available for CRM-mirrored ones.
    assert fetched.crm_engagement_id is None


def test_engagement_requiredness_is_registry_declared() -> None:
    # The working columns are API-required but database-nullable: migration
    # 0014 carries pre-PI-010 anchor rows that had none of them.
    by_name = {
        (spec.entity_type, spec.field_name): spec for spec in built_in_fields([Engagement])
    }
    for field_name in ("engagementName", "engagementStatus", "clientID"):
        assert by_name[("engagement", field_name)].required_flag, field_name
    # Pre-assignment statuses (Pending Acceptance / Assignment Declined) mean
    # a mentor is genuinely optional, not merely backfill-nullable.
    assert not by_name[("engagement", "crmMentorRefID")].required_flag
    assert not by_name[("engagement", "crmEngagementID")].required_flag
    assert by_name[("engagement", "engagementSummary")].field_type == "richText"


# --- REQ-074/079/082 + WTK-182: the session -----------------------------------------


def test_session_carries_notes_action_items_and_conference_link(session: Session) -> None:
    # Notes and action items are entered ON the session (REQ-074) as rich
    # text; the conference link is a session fact (REQ-079).
    engagement = _engagement(session)
    session.add(
        MentoringSession(
            engagement_id=engagement.engagement_id,
            scheduled_at=datetime(2026, 7, 1, 15, 0, tzinfo=UTC),
            conference_link="https://meet.example/cbm-acme",
            session_notes="<p>Reviewed the cash-flow forecast.</p>",
            action_items="<ul><li>Send updated deck</li></ul>",
        )
    )
    session.commit()

    fetched = session.scalars(select(MentoringSession)).one()
    assert fetched.engagement.engagement_name == "Acme mentoring"
    assert fetched.conference_link == "https://meet.example/cbm-acme"
    assert fetched.action_items is not None and "<ul>" in fetched.action_items
    assert engagement.sessions == [fetched]


def test_action_items_are_a_field_not_task_records() -> None:
    # REQ-082: a rich-text bulleted field on the session, NO structured task
    # records — the PI-009 nextStep/meetingNote tables are gone, their
    # concepts folded onto the session fields.
    by_name = {spec.field_name: spec for spec in built_in_fields([MentoringSession])}
    assert by_name["actionItems"].field_type == "richText"
    assert by_name["sessionNotes"].field_type == "richText"
    for retired in ("nextStep", "meetingNote"):
        assert retired not in Base.metadata.tables


def test_session_requires_a_real_engagement(session: Session) -> None:
    orphan = MentoringSession(
        engagement_id=uuid.uuid4(),
        scheduled_at=datetime(2026, 7, 1, 15, 0, tzinfo=UTC),
    )
    session.add(orphan)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_session_status_vocabulary_has_no_cancelled_value() -> None:
    # Cancellation is a soft delete (evidence retained), not a status — that
    # is what keeps the REQ-072 aggregates truthful over live rows.
    assert [name for name, _ in SESSION_STATUS_VALUES] == ["scheduled", "completed"]


def test_transcript_is_append_only(session: Session) -> None:
    # WTK-182: a captured transcript may be extended, never rewritten or
    # cleared — enforced at the persistence boundary.
    engagement = _engagement(session)
    record = MentoringSession(
        engagement_id=engagement.engagement_id,
        scheduled_at=datetime(2026, 7, 1, 15, 0, tzinfo=UTC),
        transcript_text="[00:00] Welcome.",
        transcript_source="zoom-vtt-upload",
    )
    session.add(record)
    session.commit()

    record.transcript_text = "[00:00] Welcome. [00:05] Agenda."
    session.commit()

    with pytest.raises(ValueError, match="append-only"):
        record.transcript_text = "[00:00] Rewritten."
    with pytest.raises(ValueError, match="append-only"):
        record.transcript_text = None
    # The refused assignments never reached the row.
    fetched = session.scalars(select(MentoringSession)).one()
    assert fetched.transcript_text == "[00:00] Welcome. [00:05] Agenda."


# --- REQ-084/REQ-085: resources and events ------------------------------------------


def test_resource_kinds_are_the_req084_vocabulary(session: Session) -> None:
    assert [name for name, _ in RESOURCE_KIND_VALUES] == ["document", "video", "link"]
    session.add(
        Resource(
            resource_title="Pricing worksheet",
            resource_location="https://drive.example/pricing.xlsx",
            resource_description="Template for the pricing session.",
        )
    )
    session.commit()
    assert session.scalars(select(Resource)).one().resource_id.version == 7


def test_event_carries_title_time_location_audience(session: Session) -> None:
    session.add(
        Event(
            event_title="Mentor roundtable",
            starts_at=datetime(2026, 8, 12, 17, 30, tzinfo=UTC),
            event_location="CBM office",
            event_audience="All mentors",
        )
    )
    session.commit()
    fetched = session.scalars(select(Event)).one()
    assert fetched.event_title == "Mentor roundtable"
    assert fetched.starts_at is not None


# --- Registry integration across the domain -----------------------------------------


def test_seed_registers_the_domain_and_its_option_sets(session: Session) -> None:
    result = seed_built_in_registry(session, [CrmCompanyRef, CrmMentorRef, *_DOMAIN_ENTITIES])
    assert result.retired == ()
    option_sets = set(
        session.scalars(select(OptionSet.option_set_name).where(OptionSet.deleted_at.is_(None)))
    )
    assert {"engagementStatus", "sessionStatus", "resourceKind"} <= option_sets
    # DB-R2b re-appearances: every FK carries the exact name of the key it
    # references, and each appearance has its own registry row.
    specs = {
        (spec.entity_type, spec.field_name): spec
        for spec in built_in_fields([Client, Partner, Engagement, MentoringSession])
    }
    for entity_type in ("client", "partner"):
        assert specs[(entity_type, "crmCompanyRefID")].r2b_reappearance
    assert specs[("engagement", "clientID")].r2b_reappearance
    assert specs[("session", "engagementID")].r2b_reappearance
