"""The REQ-072 engagement triage read: derivable columns, server-side (WTK-185).

Exercises :mod:`mentorapp.storage.triage` end to end on a real store: seeded
registry + generated read views + live rows, executed through the same
validated admin-SQL path the seeded data sources run under.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.storage import (
    CONDITION_OPERATORS,
    ENGAGEMENT_STATUS_VALUES,
    ENGAGEMENT_TRIAGE_COLUMNS,
    ENGAGEMENT_TRIAGE_FORMATTING_RULES,
    FORMATTING_EFFECTS,
    STATUS_COLOR_SLOTS,
    AdminSqlSource,
    AppUser,
    Client,
    CrmCompanyRef,
    CrmMentorRef,
    Engagement,
    MentoringSession,
    OptionValue,
    engagement_triage_rows,
    engagement_triage_sql,
    regenerate_read_views,
    seed_built_in_registry,
    validate_admin_sql,
)
from mentorapp.storage.columns import FormattingRuleSpec

_PAST = datetime(2026, 1, 10, 15, 0, tzinfo=UTC)
_LATER_PAST = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
_FUTURE = datetime(2030, 9, 1, 15, 0, tzinfo=UTC)
_LATER_FUTURE = datetime(2030, 11, 1, 15, 0, tzinfo=UTC)


def _mentor(session: Session, tag: str) -> tuple[AppUser, CrmMentorRef]:
    user = AppUser(crm_user_id=f"crm-{tag}", username=f"{tag}@example.org")
    session.add(user)
    session.flush()
    anchor = CrmMentorRef(crm_mentor_id=f"mentor-{tag}", user_id=user.user_id)
    session.add(anchor)
    session.flush()
    return user, anchor


def _engagement(
    session: Session,
    name: str,
    mentor: CrmMentorRef | None,
    *,
    status_name: str = "active",
) -> Engagement:
    company = CrmCompanyRef(crm_company_id=f"acct-{name}")
    session.add(company)
    session.flush()
    client = Client(crm_company_ref_id=company.crm_company_ref_id)
    session.add(client)
    session.flush()
    status_id = session.scalars(
        select(OptionValue.option_value_id).where(OptionValue.option_value_name == status_name)
    ).one()
    engagement = Engagement(
        engagement_name=name,
        engagement_status=status_id,
        client_id=client.client_id,
        crm_mentor_ref_id=mentor.crm_mentor_ref_id if mentor is not None else None,
        primary_contact_name=f"Contact {name}",
        primary_contact_email=f"contact@{name}.example",
    )
    session.add(engagement)
    session.flush()
    return engagement


def _session(session: Session, engagement: Engagement, at: datetime) -> None:
    session.add(MentoringSession(engagement_id=engagement.engagement_id, scheduled_at=at))
    session.flush()


def _prepared_store(session: Session) -> tuple[AppUser, AppUser]:
    # Explicit list, never a Base.registry sweep (the 0008 stance): the
    # shared test Base carries throwaway entities from other modules.
    seed_built_in_registry(
        session, [Client, CrmCompanyRef, CrmMentorRef, Engagement, MentoringSession]
    )
    regenerate_read_views(session)
    user_a, mentor_a = _mentor(session, "alice")
    user_b, mentor_b = _mentor(session, "bram")
    acme = _engagement(session, "acme", mentor_a)
    _session(session, acme, _PAST)
    _session(session, acme, _LATER_PAST)
    _session(session, acme, _FUTURE)
    _session(session, acme, _LATER_FUTURE)
    zenith = _engagement(session, "zenith", mentor_b)
    _session(session, zenith, _PAST)
    # Unassigned engagement (Pending Acceptance): leadership-only visibility.
    _engagement(session, "newco", None, status_name="pendingAcceptance")
    session.commit()
    return user_a, user_b


def test_triage_sql_is_a_valid_admin_source_in_both_forms() -> None:
    # The SQL is stored verbatim as seeded dataSource bodies, so it must
    # pass the executor's validation gate by construction.
    validate_admin_sql(
        AdminSqlSource(
            data_source_key="t",
            sql_text=engagement_triage_sql(mentor_scoped=True),
            user_scoped_flag=True,
        )
    )
    validate_admin_sql(
        AdminSqlSource(
            data_source_key="t",
            sql_text=engagement_triage_sql(mentor_scoped=False),
            user_scoped_flag=False,
        )
    )


def test_formatting_rules_cover_every_status_with_status_slots_only() -> None:
    """The seeded D7 rules speak the persisted vocabulary and miss no status.

    REQ-045: a rule's effect names one of the FIXED three status slots —
    never a literal color — and the triage chip rules must cover the whole
    seeded engagement-status vocabulary so no label falls back to plain text.
    """
    covered = {rule.condition_value for rule in ENGAGEMENT_TRIAGE_FORMATTING_RULES}
    assert covered == {label for _, label in ENGAGEMENT_STATUS_VALUES}
    for rule in ENGAGEMENT_TRIAGE_FORMATTING_RULES:
        assert rule.condition_field == "engagementStatusLabel"
        assert rule.condition_operator in CONDITION_OPERATORS
        assert rule.effect in FORMATTING_EFFECTS
        assert rule.effect_slot in STATUS_COLOR_SLOTS


def test_formatting_rule_spec_refuses_off_vocabulary_declarations() -> None:
    # Declaration-time gate (the ColumnSpec stance): a rule outside the
    # persisted vocabulary is a source-controlled defect — refuse at import,
    # matching the API surface's unknownSlot/unknownOperator refusals.
    with pytest.raises(ValueError, match="not a status color slot"):
        FormattingRuleSpec("f", "equals", "x", "accent", "#ff0000")
    with pytest.raises(ValueError, match="not a formatting effect"):
        FormattingRuleSpec("f", "equals", "x", "chip", "statusWarning")
    with pytest.raises(ValueError, match="not a condition operator"):
        FormattingRuleSpec("f", "matches", "x", "accent", "statusWarning")
    # Presence operators test the field itself; comparisons need a value.
    with pytest.raises(ValueError, match="conditionValue must be None"):
        FormattingRuleSpec("f", "isEmpty", "x", "accent", "statusWarning")
    with pytest.raises(ValueError, match="conditionValue is required"):
        FormattingRuleSpec("f", "equals", None, "accent", "statusWarning")


def test_triage_columns_are_derived_per_engagement(session: Session) -> None:
    # REQ-072: name, status, contact, last/next session dates, and total
    # sessions — all computed server-side from live rows, never stored.
    user_a, _user_b = _prepared_store(session)
    rows = engagement_triage_rows(session, current_user_id=user_a.user_id)
    assert len(rows) == 1
    row: dict[str, Any] = rows[0]
    assert {spec.field_name for spec in ENGAGEMENT_TRIAGE_COLUMNS} <= set(row)
    assert row["engagementName"] == "acme"
    assert row["engagementStatusLabel"] == "Active"
    assert row["primaryContactName"] == "Contact acme"
    assert row["primaryContactEmail"] == "contact@acme.example"
    assert row["totalSessions"] == 4
    # Last = the most recent past session; next = the nearest future one.
    assert str(row["lastSessionAt"]).startswith("2026-03-02")
    assert str(row["nextSessionAt"]).startswith("2030-09-01")


def test_cancelled_sessions_leave_the_aggregates(session: Session) -> None:
    # Cancellation is a soft delete; the views serve live rows only, so the
    # counts and dates move the moment a session is cancelled.
    user_a, _user_b = _prepared_store(session)
    cancelled = session.scalars(
        select(MentoringSession).where(MentoringSession.scheduled_at == _LATER_PAST)
    ).one()
    cancelled.soft_delete()
    session.commit()

    row = engagement_triage_rows(session, current_user_id=user_a.user_id)[0]
    assert row["totalSessions"] == 3
    assert str(row["lastSessionAt"]).startswith("2026-01-10")


def test_engagement_with_no_sessions_reads_zero_not_missing(session: Session) -> None:
    _user_a, user_b = _prepared_store(session)
    zenith_only = engagement_triage_rows(session, current_user_id=user_b.user_id)
    assert [r["engagementName"] for r in zenith_only] == ["zenith"]

    held = session.scalars(
        select(MentoringSession).where(MentoringSession.scheduled_at == _PAST)
    ).all()
    for row in held:
        row.soft_delete()
    session.commit()
    refreshed = engagement_triage_rows(session, current_user_id=user_b.user_id)[0]
    assert refreshed["totalSessions"] == 0
    assert refreshed["lastSessionAt"] is None
    assert refreshed["nextSessionAt"] is None


def test_mentor_scoped_read_confines_rows_to_the_session_user(session: Session) -> None:
    # WTK-186's storage-level guarantee: the scoping is IN the read, bound
    # server-side — mentor A's read simply contains no mentor-B row.
    user_a, user_b = _prepared_store(session)
    names_a = [
        r["engagementName"]
        for r in engagement_triage_rows(session, current_user_id=user_a.user_id)
    ]
    names_b = [
        r["engagementName"]
        for r in engagement_triage_rows(session, current_user_id=user_b.user_id)
    ]
    assert names_a == ["acme"]
    assert names_b == ["zenith"]
    # A user with no mentor pairing sees nothing, not everything.
    assert engagement_triage_rows(session, current_user_id=uuid.uuid4()) == []


def test_leadership_read_spans_mentors_and_the_unassigned(session: Session) -> None:
    user_a, _user_b = _prepared_store(session)
    rows = engagement_triage_rows(session, current_user_id=user_a.user_id, mentor_scoped=False)
    # Triage order (the PI-010 surfaces ruling on REQ-072): the pending
    # acceptance leads, then the imminent next session (acme has a future
    # one), then no-next-session engagements (zenith).
    assert [r["engagementName"] for r in rows] == ["newco", "acme", "zenith"]
    by_name = {r["engagementName"]: r for r in rows}
    assert by_name["newco"]["engagementStatusLabel"] == "Pending Acceptance"
    assert by_name["newco"]["totalSessions"] == 0


def test_triage_orders_by_the_ruled_priority(session: Session) -> None:
    # PI-010 surfaces ruling on REQ-072: pending acceptances, then imminent
    # sessions, then open action items — never plain name order.
    seed_built_in_registry(
        session, [Client, CrmCompanyRef, CrmMentorRef, Engagement, MentoringSession]
    )
    regenerate_read_views(session)
    user_a, mentor_a = _mentor(session, "prio")
    with_items = _engagement(session, "zz-items", mentor_a)
    _session(session, with_items, _PAST)
    item_session = session.scalars(
        select(MentoringSession).where(
            MentoringSession.engagement_id == with_items.engagement_id
        )
    ).one()
    item_session.action_items = "<ul><li>Open item</li></ul>"
    quiet = _engagement(session, "aa-quiet", mentor_a)
    _session(session, quiet, _PAST)
    imminent = _engagement(session, "mm-imminent", mentor_a)
    _session(session, imminent, _FUTURE)
    pending = _engagement(session, "nn-pending", mentor_a, status_name="pendingAcceptance")
    session.commit()

    rows = engagement_triage_rows(session, current_user_id=user_a.user_id)
    assert [r["engagementName"] for r in rows] == [
        pending.engagement_name,
        imminent.engagement_name,
        with_items.engagement_name,
        quiet.engagement_name,
    ]
    by_name = {r["engagementName"]: r for r in rows}
    assert by_name["zz-items"]["openActionItems"] == 1
    assert by_name["aa-quiet"]["openActionItems"] == 0
