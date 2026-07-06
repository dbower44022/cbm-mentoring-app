"""Relationship lookup control design gate: type-ahead + affordances (WTK-060)."""

from __future__ import annotations

import pytest

from mentorapp.api.grid_surface import MIN_SEARCH_LENGTH
from mentorapp.storage import SELECTION_CONTRACTS
from mentorapp.ui.lookup_control import (
    CREATE_RELATED_RECORD,
    LOOKUP_CONTROL,
    LOOKUP_FIELD_TYPE,
    OPEN_LINKED_RECORD,
    SUGGESTION_WINDOW,
    NothingLinkedError,
    adopt_created_record,
    matches_summary,
    open_linked_record,
    related_entity_type,
    resolve_suggestions,
)
from mentorapp.ui.record_preview import RecordRef

MENTOR_1 = RecordRef("mentor", "r-1", "Ada Lovelace")
MENTOR_2 = RecordRef("mentor", "r-2", "Grace Hopper")


def _mentors(count: int) -> list[RecordRef]:
    return [RecordRef("mentor", f"r-{n}", f"Mentor {n}") for n in range(count)]


# --- The control declaration (REQ-036) --------------------------------------------


def test_control_is_registry_driven_and_reuses_the_one_search_threshold() -> None:
    assert LOOKUP_CONTROL.field_type == LOOKUP_FIELD_TYPE == "reference"
    # REQ-020's 3rd-character rule, inherited — never a second threshold.
    assert LOOKUP_CONTROL.min_search_length == MIN_SEARCH_LENGTH
    assert LOOKUP_CONTROL.affordances == ("OpenLinkedRecord", "CreateRelatedRecord")
    assert LOOKUP_CONTROL.create_adopts_new_record is True
    # The form writes the FK only; the display title never round-trips.
    assert LOOKUP_CONTROL.value_written == "recordID"


def test_related_entity_comes_from_the_entity_named_key() -> None:
    assert related_entity_type("mentorID") == "mentor"
    assert related_entity_type("crmEngagementRefID") == "crmEngagementRef"
    for bad_name in ("status", "ID", "mentorId"):
        with pytest.raises(ValueError, match="entity-named"):
            related_entity_type(bad_name)


def test_affordances_speak_the_grid_action_vocabulary() -> None:
    for action in (OPEN_LINKED_RECORD, CREATE_RELATED_RECORD):
        assert action.selection_contract in SELECTION_CONTRACTS
    assert (OPEN_LINKED_RECORD.selection_contract, OPEN_LINKED_RECORD.classification) == (
        "single",
        "safe",
    )
    # Creating a record is modifying, never destructive — no confirmation.
    assert (CREATE_RELATED_RECORD.selection_contract, CREATE_RELATED_RECORD.classification) == (
        "none",
        "modifying",
    )


# --- Type-ahead suggestions (server-side truth) ------------------------------------


def test_empty_text_is_idle_and_short_text_educates_instead_of_searching() -> None:
    idle = resolve_suggestions("", related_label="Mentor", data_source_key="mentors")
    assert (idle.phase, idle.message) == ("idle", None)
    short = resolve_suggestions("ad", related_label="Mentor", data_source_key="mentors")
    assert short.phase == "keepTyping"
    assert str(MIN_SEARCH_LENGTH) in short.message.why


def test_matches_window_renders_but_the_full_count_is_the_truth() -> None:
    outcome = resolve_suggestions(
        "men",
        related_label="Mentor",
        data_source_key="mentors",
        matches=_mentors(SUGGESTION_WINDOW + 4),
        total_matches=37,
    )
    assert outcome.phase == "matches"
    assert len(outcome.suggestions) == SUGGESTION_WINDOW
    assert outcome.total_matches == 37
    assert outcome.summary == matches_summary(37, SUGGESTION_WINDOW)
    assert "37 matches" in outcome.summary


def test_a_window_larger_than_the_total_is_a_wiring_bug() -> None:
    with pytest.raises(ValueError, match="total_matches"):
        resolve_suggestions(
            "men",
            related_label="Mentor",
            data_source_key="mentors",
            matches=[MENTOR_1, MENTOR_2],
            total_matches=1,
        )


def test_no_matches_names_create_new_never_a_dead_end() -> None:
    outcome = resolve_suggestions(
        "zzz", related_label="Mentor", data_source_key="mentors", matches=()
    )
    assert outcome.phase == "noMatches"
    assert "'zzz'" in outcome.message.what_happened
    assert "New…" in outcome.message.what_next


def test_no_access_explains_the_missing_grant_never_hides_the_control() -> None:
    outcome = resolve_suggestions(
        "ada",
        related_label="Mentor",
        data_source_key="mentors",
        has_access=False,
        matches=[MENTOR_1],
    )
    assert (outcome.phase, outcome.suggestions) == ("noAccess", ())
    assert "'mentors' data source" in outcome.message.why
    assert "administrator" in outcome.message.what_next


# --- The two inline affordances (REQ-036) ------------------------------------------


def test_open_hands_the_linked_record_to_the_pop_out_machinery() -> None:
    assert open_linked_record(MENTOR_1, field_label="Mentor") == MENTOR_1


def test_open_on_an_empty_field_explains_instead_of_no_op() -> None:
    with pytest.raises(NothingLinkedError) as caught:
        open_linked_record(None, field_label="Mentor")
    message = caught.value.message
    assert "no Mentor record" in message.what_happened
    assert "New…" in message.what_next


def test_create_new_adopts_the_created_record_in_place() -> None:
    assert adopt_created_record(MENTOR_2, field_name="mentorID") == MENTOR_2


def test_create_new_for_the_wrong_entity_fails_loudly() -> None:
    stranger = RecordRef("mentee", "x-1", "Not a mentor")
    with pytest.raises(ValueError, match="links 'mentor'"):
        adopt_created_record(stranger, field_name="mentorID")
