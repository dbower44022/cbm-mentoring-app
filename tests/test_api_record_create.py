"""Create-record flow with duplicate detection design (REQ-037, WTK-062).

The flow controller is pure over wire-shaped field settings and records, so
most tests need no database; the two write-engine additions it composes —
the deleted-inclusive advisory match and the restore write — are exercised
against the real engine with PostalCode as the guinea-pig entity, exactly as
``test_api_processes`` does.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.api import (
    CREATE_FORM,
    CREATE_LANDING,
    CommitCreate,
    CreateFlow,
    CreateSaved,
    DirtyWindowGuard,
    RestoreInsteadOfCreate,
    SaveNotice,
    SimilarCandidate,
    StaleRowVersionError,
    SwitchToExisting,
    ValidationSweep,
    create_form_seed,
    find_similar_records,
    identity_field_names,
    match_rule_fields,
    registry_for,
    restore_record,
)
from mentorapp.api.edit_safety import CloseAllowed
from mentorapp.storage import ChangeFeedEntry, PostalCode, SchemaRegistry, utcnow

ENTITY = "postalCode"


def _field(name: str, **overrides: Any) -> dict[str, Any]:
    """One wire field payload as ``GET /schema/{entity}`` serves it."""
    return {
        "fieldName": name,
        "fieldType": "text",
        "fieldLabel": name,
        "requiredFlag": False,
        "validationRules": None,
        "defaultValue": None,
        "optionSet": None,
        **overrides,
    }


FIELDS = (
    _field("postalCodeValue", requiredFlag=True),
    _field(
        "cityName",
        requiredFlag=True,
        validationRules={"duplicateMatchRules": ["byCityState"]},
    ),
    _field("stateCode", validationRules={"duplicateMatchRules": ["byCityState"]}),
    _field("countryCode", defaultValue="US"),
)


def _flow() -> CreateFlow:
    return CreateFlow("w1", ENTITY, FIELDS)


def _record(row_version: int = 1, **fields: Any) -> dict[str, Any]:
    return {
        "postalCodeID": "0197-existing",
        "rowVersion": row_version,
        "deletedAt": None,
        **fields,
    }


# --- The declared form ---------------------------------------------------------------


def test_create_form_declares_the_same_full_screen_form() -> None:
    # REQ-037's shape as declared properties: the shell can never substitute a
    # wizard, gate Save on the similar check, or land anywhere but the read view.
    assert CREATE_FORM.kind == "fullScreenForm"
    assert CREATE_FORM.opens == "empty"
    assert CREATE_FORM.prefill_source == "defaultValue"
    assert CREATE_FORM.validation == "sharedFormEngine"
    assert CREATE_FORM.similar_check == "nonBlocking"
    assert CREATE_FORM.comparison == "sideBySide"
    assert CREATE_FORM.lands_on == CREATE_LANDING == "readView"
    assert CREATE_FORM.cancel_creates == "nothing"


def test_seed_prefills_only_settings_declared_defaults() -> None:
    assert create_form_seed(FIELDS) == {"countryCode": "US"}
    assert _flow().values == {"countryCode": "US"}


def test_identity_fields_derive_from_registry_match_rules() -> None:
    assert match_rule_fields(FIELDS) == {"byCityState": frozenset({"cityName", "stateCode"})}
    assert identity_field_names(FIELDS) == frozenset({"cityName", "stateCode"})


# --- The non-blocking similar check --------------------------------------------------


def test_similar_check_fires_once_per_completed_identity() -> None:
    flow = _flow()
    assert flow.similar_check_input() is None  # no rule fully supplied yet
    flow.edit_value("cityName", "Springfield")
    assert flow.similar_check_input() is None  # byCityState still missing stateCode
    flow.edit_value("stateCode", "  ")  # blank text is NO value
    assert flow.similar_check_input() is None
    flow.edit_value("stateCode", "IL")
    assert flow.similar_check_input() == {"cityName": "Springfield", "stateCode": "IL"}
    assert flow.similar_check_input() is None  # unchanged identity: no re-fire
    flow.edit_value("stateCode", "OR")
    assert flow.similar_check_input() == {"cityName": "Springfield", "stateCode": "OR"}


def test_offer_shapes_candidates_side_by_side_and_never_blocks() -> None:
    flow = _flow()
    assert flow.offer_similar([]) is None  # a clean create hears nothing
    offer = flow.offer_similar(
        [_record(), _record(2, postalCodeID="0197-removed", deletedAt="2026-07-01T00:00:00Z")]
    )
    assert offer is not None
    assert offer.comparison == "sideBySide"
    assert offer.blocking is False
    assert offer.enforced is False
    assert [candidate.removed for candidate in offer.candidates] == [False, True]
    assert offer.offers_restore is True


# --- Validation parity and the save --------------------------------------------------


def test_save_sweeps_with_the_shared_engine_before_posting() -> None:
    flow = _flow()
    flow.edit_value("cityName", "Springfield")
    outcome = flow.request_save()
    # postalCodeValue is required and untouched: the sweep reports it exactly
    # as the edit form's sweep would — same engine, same entry, first focused.
    assert isinstance(outcome, ValidationSweep)
    assert [error["fieldName"] for error in outcome.inline] == ["postalCodeValue"]
    assert outcome.focus_field_name == "postalCodeValue"


def test_clean_save_posts_normalized_values_and_lands_on_the_read_view() -> None:
    flow = _flow()
    flow.edit_value("postalCodeValue", "62704")
    flow.edit_value("cityName", "Springfield")
    flow.edit_value("stateCode", "   ")  # cleared control: never travels
    commit = flow.request_save()
    assert commit == CommitCreate(
        ENTITY, {"postalCodeValue": "62704", "cityName": "Springfield", "countryCode": "US"}
    )
    saved = flow.save_succeeded({"postalCodeID": "0197-new", "rowVersion": 1})
    assert isinstance(saved, CreateSaved)
    assert saved.destination == CREATE_LANDING
    assert saved.notice == SaveNotice(ENTITY, "0197-new", 1, "created")


def test_server_rejection_then_continue_resubmits_with_recorded_override() -> None:
    flow = _flow()
    flow.edit_value("postalCodeValue", "62704")
    flow.edit_value("cityName", "Springfield")
    offer = flow.save_rejected_duplicates([_record()])
    assert offer.enforced is True
    assert offer.blocking is False  # even the enforced offer is a choice, not a wall
    resubmit = flow.choose_continue()
    assert isinstance(resubmit, CommitCreate)
    assert resubmit.override_duplicates is True
    assert resubmit.override_reason == "userContinuedPastDuplicateOffer"


def test_editing_after_a_rejection_voids_the_pending_override() -> None:
    flow = _flow()
    flow.edit_value("postalCodeValue", "62704")
    flow.edit_value("cityName", "Springfield")
    flow.save_rejected_duplicates([_record()])
    flow.edit_value("cityName", "Springfield East")  # a NEW payload faces detection again
    assert flow.choose_continue() is None
    commit = flow.request_save()
    assert isinstance(commit, CommitCreate)
    assert commit.override_duplicates is False


def test_continue_past_the_advisory_offer_is_a_dismissal() -> None:
    flow = _flow()
    flow.offer_similar([_record()])
    assert flow.choose_continue() is None


# --- Switch and restore --------------------------------------------------------------


def test_switch_opens_the_existing_read_view_and_creates_nothing() -> None:
    assert _flow().choose_switch("0197-existing") == SwitchToExisting(
        ENTITY, "0197-existing", CREATE_LANDING
    )


def test_restore_is_offered_only_for_a_removed_candidate() -> None:
    flow = _flow()
    removed = SimilarCandidate(
        _record(3, postalCodeID="0197-removed", deletedAt="2026-07-01T00:00:00Z"), removed=True
    )
    assert flow.choose_restore(removed) == RestoreInsteadOfCreate(
        ENTITY, "0197-removed", 3, CREATE_LANDING
    )
    with pytest.raises(ValueError, match="removed"):
        flow.choose_restore(SimilarCandidate(_record(), removed=False))


# --- Cancel and close ----------------------------------------------------------------


def test_close_guards_authored_input_but_defaults_close_freely() -> None:
    flow = _flow()
    assert isinstance(flow.request_close(), CloseAllowed)  # still at seed: nothing at risk
    flow.edit_value("cityName", "Springfield")
    guard = flow.request_close()
    assert isinstance(guard, DirtyWindowGuard)
    assert guard.dirty_fields == ("cityName",)
    flow.cancel()  # Cancel IS the discard: back to seed, closes freely
    assert isinstance(flow.request_close(), CloseAllowed)
    assert flow.values == {"countryCode": "US"}


# --- The engine halves the flow composes ---------------------------------------------


def _register(session: Session, field_name: str, **overrides: Any) -> None:
    session.add(
        SchemaRegistry(
            entity_type=ENTITY,
            field_name=field_name,
            field_type="text",
            field_label=field_name,
            **overrides,
        )
    )


@pytest.fixture()
def match_registry(session: Session) -> None:
    _register(session, "cityName", validation_rules={"duplicateMatchRules": ["byCityState"]})
    _register(session, "stateCode", validation_rules={"duplicateMatchRules": ["byCityState"]})
    session.flush()


def test_advisory_check_includes_removed_matches(
    session: Session, match_registry: None
) -> None:
    live = PostalCode(postal_code_value="62704", city_name="Springfield", state_code="IL")
    removed = PostalCode(postal_code_value="62705", city_name="Springfield", state_code="IL")
    removed.deleted_at = utcnow()
    session.add_all([live, removed])
    session.flush()
    registry = registry_for(session, ENTITY)
    values = {"cityName": "springfield", "stateCode": "il"}

    enforced, rules = find_similar_records(session, PostalCode, registry, values)
    assert enforced == [live]  # create-time enforcement stays live-only (DB-S3)
    assert rules == ["byCityState"]

    advisory, _ = find_similar_records(
        session, PostalCode, registry, values, include_deleted=True
    )
    assert {record.postal_code_value for record in advisory} == {"62704", "62705"}


def test_restore_clears_the_soft_delete_and_feeds_restored(session: Session) -> None:
    row = PostalCode(postal_code_value="62704", city_name="Springfield", state_code="IL")
    session.add(row)
    session.flush()
    row.deleted_at = utcnow()
    session.flush()

    with pytest.raises(StaleRowVersionError):
        restore_record(session, row, ENTITY, row_version=row.row_version - 1)

    restored = restore_record(session, row, ENTITY, row_version=row.row_version)
    assert restored.deleted_at is None
    assert restored.deleted_by is None
    kinds = session.scalars(select(ChangeFeedEntry.change_kind)).all()
    assert "restored" in kinds

    # Racing an already-landed restore is a no-op, not an error: the record
    # being back is exactly what the caller wanted.
    version = restored.row_version
    assert restore_record(session, restored, ENTITY, row_version=version) is restored
    assert restored.row_version == version
