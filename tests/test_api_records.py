"""``/records/{type}/{id}/preview`` — the record-window content surface (REQ-012)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Mapped, Session, mapped_column

from identity_stub import header_user_id
from mentorapp.access import InMemoryLookupSources, LookupBinding, grant_data_source_role
from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.routers.records import get_lookup_sources, get_record_catalog
from mentorapp.api.routers.workprocess import get_role_source
from mentorapp.main import create_app
from mentorapp.storage import (
    BaseEntity,
    ChangeFeedEntry,
    DataSource,
    DuplicateOverride,
    FieldChange,
    SchemaRegistry,
    entity_key,
    utcnow,
    uuid7,
)
from mentorapp.storage.mentoring import ProgressGoal


class PreviewMentor(BaseEntity):
    __tablename__ = "PreviewMentor"

    preview_mentor_id: Mapped[uuid.UUID] = entity_key("previewMentorID")
    mentor_name: Mapped[str] = mapped_column("mentorName", nullable=False)


@dataclass(frozen=True)
class StubCatalog:
    """The entity catalog, stubbed: the wire names these tests serve."""

    entities: Mapping[str, type[Any]] = field(
        default_factory=lambda: {"mentor": PreviewMentor, "progressGoal": ProgressGoal}
    )

    def entity_class(self, entity_type: str) -> type[Any] | None:
        return self.entities.get(entity_type)


@dataclass(frozen=True)
class StubRoles:
    """Every user is a mentor: these are not role-resolution tests."""

    roles: frozenset[str] = frozenset({"mentor"})

    def user_roles(self, user_id: uuid.UUID) -> frozenset[str]:
        return self.roles


LOOKUP_SOURCE_KEY = "progressGoalsForLookup"


@pytest.fixture()
def client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    # The D9 identity seam resolves sessions in production; these are not
    # session-lifecycle tests, so the stub names the acting user directly.
    app.dependency_overrides[get_current_user_id] = header_user_id
    app.dependency_overrides[get_record_catalog] = lambda: StubCatalog()
    app.dependency_overrides[get_role_source] = lambda: StubRoles()
    app.dependency_overrides[get_lookup_sources] = lambda: InMemoryLookupSources(
        [LookupBinding("progressGoal", LOOKUP_SOURCE_KEY)]
    )
    return TestClient(app)


@pytest.fixture()
def user_id() -> uuid.UUID:
    return uuid7()


def _headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-ID": str(user_id)}


def _mentor(session: Session, **overrides: Any) -> PreviewMentor:
    record = PreviewMentor(**{"mentor_name": "Ada Lovelace", **overrides})
    session.add(record)
    session.flush()
    return record


def _get_preview(
    client: TestClient, user_id: uuid.UUID, record_id: uuid.UUID, entity_type: str = "mentor"
) -> Any:
    return client.get(f"/records/{entity_type}/{record_id}/preview", headers=_headers(user_id))


# --- The read-optimized content (REQ-012) ------------------------------------------


def test_preview_serves_the_pane_declaration_and_flat_record(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    record = _mentor(session)
    response = _get_preview(client, user_id, record.preview_mentor_id)
    assert response.status_code == 200
    body = response.json()
    assert body["errors"] is None
    pane = body["data"]["pane"]
    # The WTK-021 declarations, verbatim: read mode, zero edit controls, the
    # two edit paths, right dock.
    assert pane["readOptimized"] is True
    assert pane["editControls"] is False
    assert pane["editPaths"] == ["editAction", "perFieldDoubleClick"]
    assert pane["dockPosition"] == "right"
    served = body["data"]["record"]
    assert served["previewMentorID"] == str(record.preview_mentor_id)
    assert served["mentorName"] == "Ada Lovelace"
    # rowVersion rides along so any read can lead straight to an edit (DB-S4).
    assert served["rowVersion"] == 1
    assert body["data"]["notice"] is None


def test_custom_attributes_ride_flat_in_the_record(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    record = _mentor(session, custom_attributes={"favoriteColor": "teal"})
    served = _get_preview(client, user_id, record.preview_mentor_id).json()["data"]["record"]
    # DB-R3: custom attributes are indistinguishable from built-ins; the raw
    # bag never reaches the wire.
    assert served["favoriteColor"] == "teal"
    assert "customAttributes" not in served


def test_pop_out_frame_is_a_real_window_with_header_minus_navigation(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    record = _mentor(session)
    body = _get_preview(client, user_id, record.preview_mentor_id).json()
    frame = body["data"]["popOutFrame"]
    assert frame["kind"] == "browserWindow"
    assert frame["hasNavigation"] is False
    assert frame["headerRight"] == ["notificationBell", "help", "accountMenu"]


# --- The three not-a-record answers -------------------------------------------------


def test_unknown_entity_type_educates_with_a_422(
    client: TestClient, user_id: uuid.UUID
) -> None:
    response = _get_preview(client, user_id, uuid7(), entity_type="gadget")
    assert response.status_code == 422
    (error,) = response.json()["errors"]
    assert error["fieldName"] == "entityType"
    assert error["code"] == "unknownEntityType"
    assert "'gadget'" in error["message"]


def test_unknown_record_is_a_404(client: TestClient, user_id: uuid.UUID) -> None:
    missing = uuid7()
    response = _get_preview(client, user_id, missing)
    assert response.status_code == 404
    (error,) = response.json()["errors"]
    assert error["code"] == "notFound"
    assert str(missing) in error["message"]


def test_soft_deleted_record_still_answers_with_an_honest_notice(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    record = _mentor(session, deleted_at=utcnow(), deleted_by=uuid7())
    response = _get_preview(client, user_id, record.preview_mentor_id)
    # A pop-out is pinned to its record: removal explains itself, never 404s
    # a window the user is looking at.
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["record"]["mentorName"] == "Ada Lovelace"
    notice = data["notice"]
    assert notice["whatHappened"] == "This record has been removed."
    # Honest soft-delete wording: kept and restorable, not destroyed.
    assert "kept, not destroyed" in notice["why"]
    assert "administrator can restore" in notice["whatNext"]


# --- The seams fail the standard ways ------------------------------------------------


def test_missing_user_header_is_the_standard_422(client: TestClient, session: Session) -> None:
    record = _mentor(session)
    response = client.get(f"/records/mentor/{record.preview_mentor_id}/preview")
    assert response.status_code == 422


def test_unwired_catalog_fails_loudly(session: Session, user_id: uuid.UUID) -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    # The D9 identity seam resolves sessions in production; these are not
    # session-lifecycle tests, so the stub names the acting user directly.
    app.dependency_overrides[get_current_user_id] = header_user_id
    # create_app now installs the production catalog (WTK-168 records wiring);
    # removing that binding recreates the unwired deployment this test pins —
    # the seam itself must still fail loudly, never serve an empty world.
    del app.dependency_overrides[get_record_catalog]
    client = TestClient(app)
    with pytest.raises(RuntimeError, match="record catalog provider is not wired"):
        _get_preview(client, user_id, uuid7())


# --- The write surface (REL-004 block 1) ---------------------------------------------


@pytest.fixture()
def write_registry(session: Session) -> None:
    """Registry rows for the ``mentor`` write tests — the field settings of record.

    ``mentorName`` carries a duplicate-match rule (text fallback path: no
    shadow column, ``lower(trim())`` inline); ``favoriteColor`` is
    user-defined so writes must land in the custom bag yet serve flat.
    """
    session.add_all(
        [
            SchemaRegistry(
                entity_type="mentor",
                field_name="mentorName",
                field_type="text",
                field_label="Name",
                required_flag=True,
                history_tracked_flag=True,
                validation_rules={"duplicateMatchRules": ["byName"]},
            ),
            SchemaRegistry(
                entity_type="mentor",
                field_name="favoriteColor",
                field_type="text",
                field_label="Favorite Color",
                user_defined_flag=True,
            ),
        ]
    )
    session.commit()


def _post_create(
    client: TestClient, user_id: uuid.UUID, values: dict[str, Any], **extra: Any
) -> Any:
    return client.post(
        "/records/mentor", headers=_headers(user_id), json={"values": values, **extra}
    )


def test_create_lands_flat_with_row_version_and_feeds(
    client: TestClient, session: Session, user_id: uuid.UUID, write_registry: None
) -> None:
    response = _post_create(
        client, user_id, {"mentorName": "Grace Hopper", "favoriteColor": "navy"}
    )
    assert response.status_code == 200
    served = response.json()["data"]["record"]
    assert served["mentorName"] == "Grace Hopper"
    # DB-R3: the custom attribute serves flat, indistinguishable from built-ins.
    assert served["favoriteColor"] == "navy"
    assert served["rowVersion"] == 1
    stored = session.get(PreviewMentor, uuid.UUID(served["previewMentorID"]))
    assert stored is not None and stored.custom_attributes == {"favoriteColor": "navy"}
    assert stored.created_by == user_id
    feed = session.scalars(select(ChangeFeedEntry)).all()
    assert [entry.change_kind for entry in feed] == ["created"]


def test_create_reports_every_field_failure_at_once(
    client: TestClient, user_id: uuid.UUID, write_registry: None
) -> None:
    response = _post_create(client, user_id, {"mentorHat": "fedora"})
    assert response.status_code == 422
    errors = {error["fieldName"]: error["code"] for error in response.json()["errors"]}
    # One round trip carries BOTH failures: the unknown field and the
    # missing required field (REQ-033's save sweep, server side).
    assert errors == {"mentorHat": "unknownField", "mentorName": "requiredField"}


def test_create_duplicate_answers_409_and_override_is_recorded(
    client: TestClient, session: Session, user_id: uuid.UUID, write_registry: None
) -> None:
    _mentor(session, mentor_name="Ada Lovelace")
    session.commit()
    rejected = _post_create(client, user_id, {"mentorName": "  ada LOVELACE "})
    # Same-name create rejects with the candidates in the body (DB-S12).
    assert rejected.status_code == 409
    (error,) = rejected.json()["errors"]
    assert error["code"] == "duplicateCandidates"

    overridden = _post_create(
        client,
        user_id,
        {"mentorName": "  ada LOVELACE "},
        overrideDuplicates=True,
        overrideReason="different person, same name",
    )
    # Continuing is allowed, and remembered (REQ-059): the override rides the
    # same transaction as the create.
    assert overridden.status_code == 200
    override = session.scalars(select(DuplicateOverride)).one()
    assert override.matched_rule_names == ["byName"]
    assert override.override_reason == "different person, same name"


def test_patch_updates_bumps_version_and_writes_history(
    client: TestClient, session: Session, user_id: uuid.UUID, write_registry: None
) -> None:
    record = _mentor(session)
    session.commit()
    response = client.patch(
        f"/records/mentor/{record.preview_mentor_id}",
        headers=_headers(user_id),
        json={"mentorName": "Ada King", "rowVersion": 1},
    )
    assert response.status_code == 200
    served = response.json()["data"]["record"]
    assert served["mentorName"] == "Ada King"
    assert served["rowVersion"] == 2
    change = session.scalars(select(FieldChange)).one()
    assert (change.field_name, change.old_value, change.new_value) == (
        "mentorName",
        "Ada Lovelace",
        "Ada King",
    )


def test_patch_stale_version_answers_409_with_the_current_record(
    client: TestClient, session: Session, user_id: uuid.UUID, write_registry: None
) -> None:
    record = _mentor(session)
    session.commit()
    response = client.patch(
        f"/records/mentor/{record.preview_mentor_id}",
        headers=_headers(user_id),
        json={"mentorName": "Ada King", "rowVersion": 7},
    )
    assert response.status_code == 409
    body = response.json()
    (error,) = body["errors"]
    assert error["code"] == "staleRowVersion"
    # The current record rides the 409 body so the client can merge, not guess.
    assert body["data"]["mentorName"] == "Ada Lovelace"
    assert body["data"]["rowVersion"] == 1


def test_patch_without_row_version_is_refused(
    client: TestClient, session: Session, user_id: uuid.UUID, write_registry: None
) -> None:
    record = _mentor(session)
    session.commit()
    response = client.patch(
        f"/records/mentor/{record.preview_mentor_id}",
        headers=_headers(user_id),
        json={"mentorName": "Ada King"},
    )
    assert response.status_code == 422
    (error,) = response.json()["errors"]
    assert error["fieldName"] == "rowVersion"


def test_patch_soft_deleted_target_is_404_not_resurrection(
    client: TestClient, session: Session, user_id: uuid.UUID, write_registry: None
) -> None:
    record = _mentor(session, deleted_at=utcnow(), deleted_by=uuid7())
    session.commit()
    response = client.patch(
        f"/records/mentor/{record.preview_mentor_id}",
        headers=_headers(user_id),
        json={"mentorName": "Ada King", "rowVersion": 1},
    )
    assert response.status_code == 404


def test_restore_brings_a_removed_record_back(
    client: TestClient, session: Session, user_id: uuid.UUID, write_registry: None
) -> None:
    record = _mentor(session, deleted_at=utcnow(), deleted_by=uuid7())
    session.commit()
    response = client.post(
        f"/records/mentor/{record.preview_mentor_id}/restore",
        headers=_headers(user_id),
        json={"rowVersion": 1},
    )
    assert response.status_code == 200
    assert response.json()["data"]["record"]["deletedAt"] is None
    session.expire_all()
    assert record.deleted_at is None
    feed = session.scalars(select(ChangeFeedEntry)).all()
    assert [entry.change_kind for entry in feed] == ["restored"]


def test_restoring_a_live_record_is_a_calm_no_op(
    client: TestClient, session: Session, user_id: uuid.UUID, write_registry: None
) -> None:
    record = _mentor(session)
    session.commit()
    response = client.post(
        f"/records/mentor/{record.preview_mentor_id}/restore",
        headers=_headers(user_id),
        json={"rowVersion": 1},
    )
    # The offer raced a restore that already happened — not an error.
    assert response.status_code == 200
    assert response.json()["data"]["record"]["rowVersion"] == 1


def test_similar_records_check_is_advisory_and_sees_removed_matches(
    client: TestClient, session: Session, user_id: uuid.UUID, write_registry: None
) -> None:
    live = _mentor(session, mentor_name="Ada Lovelace")
    removed = _mentor(session, mentor_name="Ada Lovelace", deleted_at=utcnow())
    session.commit()
    response = client.post(
        "/records/mentor/similar-records",
        headers=_headers(user_id),
        json={"values": {"mentorName": "ada lovelace"}},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    # Never a wall in front of Save: the offer is declared non-blocking.
    assert data["blocking"] is False
    assert data["matchedRuleNames"] == ["byName"]
    by_id = {
        candidate["record"]["previewMentorID"]: candidate["removed"]
        for candidate in data["candidates"]
    }
    # The deleted-inclusive advisory scan flags the removed match so the form
    # can offer restore-instead-of-create (REQ-037).
    assert by_id == {
        str(live.preview_mentor_id): False,
        str(removed.preview_mentor_id): True,
    }


def test_similar_records_without_a_complete_rule_offers_nothing(
    client: TestClient, session: Session, user_id: uuid.UUID, write_registry: None
) -> None:
    _mentor(session, mentor_name="Ada Lovelace")
    session.commit()
    response = client.post(
        "/records/mentor/similar-records",
        headers=_headers(user_id),
        json={"values": {"favoriteColor": "teal"}},
    )
    assert response.status_code == 200
    assert response.json()["data"]["candidates"] == []


# --- The edit-form view-model (REQ-032/039/040) --------------------------------------


def test_edit_form_serves_frame_record_and_dispositions(
    client: TestClient, session: Session, user_id: uuid.UUID, write_registry: None
) -> None:
    record = _mentor(session, custom_attributes={"favoriteColor": "teal"})
    session.commit()
    response = client.get(
        f"/records/mentor/{record.preview_mentor_id}/edit-form", headers=_headers(user_id)
    )
    assert response.status_code == 200
    data = response.json()["data"]
    # The frame declarations, verbatim (REQ-032 + the REQ-038 keyboard).
    screen = data["screen"]
    assert screen["presentation"] == "fullScreen"
    assert screen["fieldPositions"] == "matchesReadView"
    assert screen["save"] == {"label": "Save", "prominence": "large", "shortcut": "Ctrl+S"}
    assert screen["cancel"] == {"label": "Cancel", "behavior": "revertToOriginal"}
    assert data["keyboard"]["enter"] == "activateFocusedControl"
    assert data["keyboard"]["tab"] == "nextEditableField"
    # The record rides flat with rowVersion — the PATCH base.
    assert data["record"]["rowVersion"] == 1
    fields = {entry["fieldName"]: entry for entry in data["fields"]}
    # Registry fields are editable; the required flag reaches the form as a
    # field setting (REQ-033), and the custom attribute is indistinguishable.
    assert fields["mentorName"]["editable"] is True
    assert fields["mentorName"]["requiredFlag"] is True
    assert fields["favoriteColor"]["editable"] is True
    # Structural fields render read-only in place with click-to-explain
    # (REQ-039): system kind, educate voice, no tab stop.
    created = fields["createdAt"]
    assert created["editable"] is False
    assert created["readOnly"]["kind"] == "system"
    assert created["readOnly"]["rendering"]["tabStop"] is False
    assert "system field" in created["readOnly"]["explanation"]["why"]
    # Focus starts on the first editable field in read-view order (REQ-038).
    assert data["initialFocusField"] == "mentorName"


def test_edit_form_carries_the_help_affordance_only_where_settings_do(
    client: TestClient, session: Session, user_id: uuid.UUID
) -> None:
    session.add_all(
        [
            SchemaRegistry(
                entity_type="mentor",
                field_name="mentorName",
                field_type="text",
                field_label="Name",
                help_text="The mentor's full legal name.",
            ),
            SchemaRegistry(
                entity_type="mentor",
                field_name="favoriteColor",
                field_type="text",
                field_label="Favorite Color",
                user_defined_flag=True,
            ),
        ]
    )
    record = _mentor(session)
    session.commit()
    response = client.get(
        f"/records/mentor/{record.preview_mentor_id}/edit-form", headers=_headers(user_id)
    )
    fields = {entry["fieldName"]: entry for entry in response.json()["data"]["fields"]}
    help_ = fields["mentorName"]["help"]
    # REQ-040: a subtle affordance — marker on the label, hover/focus reveal,
    # never persistent; and NOTHING where no help text exists.
    assert help_["helpText"] == "The mentor's full legal name."
    assert help_["rendering"]["marker"] == "infoMarker"
    assert help_["rendering"]["reveal"] == ["hover", "focus"]
    assert help_["rendering"]["persistent"] is False
    assert fields["favoriteColor"]["help"] is None


def test_edit_form_for_a_removed_record_is_404(
    client: TestClient, session: Session, user_id: uuid.UUID, write_registry: None
) -> None:
    record = _mentor(session, deleted_at=utcnow(), deleted_by=uuid7())
    session.commit()
    response = client.get(
        f"/records/mentor/{record.preview_mentor_id}/edit-form", headers=_headers(user_id)
    )
    # Editing a removed record is a restore first, never a resurrection.
    assert response.status_code == 404


# --- The lookup type-ahead read (REQ-036) --------------------------------------------


@pytest.fixture()
def lookup_registry(session: Session, user_id: uuid.UUID) -> None:
    """Host field row (the label source) + searchable related field + grant."""
    session.add_all(
        [
            SchemaRegistry(
                entity_type="mentor",
                field_name="progressGoalID",
                field_type="reference",
                field_label="Progress goal",
            ),
            SchemaRegistry(
                entity_type="progressGoal",
                field_name="progressGoalDescription",
                field_type="text",
                field_label="Description",
                searchable_flag=True,
            ),
            DataSource(
                data_source_key=LOOKUP_SOURCE_KEY,
                data_source_name="Progress goals for lookup",
                data_source_sql="SELECT * FROM vwProgressGoal",
            ),
        ]
    )
    session.flush()
    grant_data_source_role(
        session,
        data_source_key=LOOKUP_SOURCE_KEY,
        role_name="mentor",
        granted_by=user_id,
    )
    session.commit()


def _lookup(client: TestClient, user_id: uuid.UUID, q: str) -> Any:
    return client.get(
        "/lookups/mentor/progressGoalID", headers=_headers(user_id), params={"q": q}
    )


def test_lookup_serves_matches_with_the_full_set_count(
    client: TestClient, session: Session, user_id: uuid.UUID, lookup_registry: None
) -> None:
    session.add_all(
        [
            ProgressGoal(progress_goal_description="Improve budgeting skills"),
            ProgressGoal(progress_goal_description="Budget review cadence"),
            ProgressGoal(progress_goal_description="Hire a bookkeeper"),
        ]
    )
    session.commit()
    response = _lookup(client, user_id, "budget")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["phase"] == "matches"
    assert data["totalMatches"] == 2
    titles = {suggestion["title"] for suggestion in data["suggestions"]}
    assert titles == {"Improve budgeting skills", "Budget review cadence"}
    assert all(s["entityType"] == "progressGoal" for s in data["suggestions"])


def test_lookup_educates_below_the_live_threshold(
    client: TestClient, user_id: uuid.UUID, lookup_registry: None
) -> None:
    response = _lookup(client, user_id, "b")
    # Short text never queries: the shared presentation rule decides.
    assert response.json()["data"]["phase"] == "keepTyping"


def test_lookup_denial_renders_as_no_access_not_an_error(
    client: TestClient, session: Session, user_id: uuid.UUID, lookup_registry: None
) -> None:
    # Revoke by granting to a role the stub user does not hold: rebuild the
    # grant world with only 'leadership'.
    from mentorapp.storage import DataSourceRoleGrant

    for grant in session.scalars(select(DataSourceRoleGrant)):
        grant.deleted_at = utcnow()
    session.commit()
    response = _lookup(client, user_id, "budget")
    # The field stays visible and explains (never hide): 200 with a phase,
    # not an HTTP refusal.
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["phase"] == "noAccess"
    assert data["message"] is not None


def test_lookup_on_an_unknown_host_field_is_refused(
    client: TestClient, user_id: uuid.UUID, lookup_registry: None
) -> None:
    response = client.get(
        "/lookups/mentor/favoriteSnackID", headers=_headers(user_id), params={"q": "x"}
    )
    assert response.status_code == 422
    (error,) = response.json()["errors"]
    assert error["code"] == "unknownField"
