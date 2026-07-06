"""Read-only field rendering design gate: in-place render + click-to-explain (WTK-064)."""

from __future__ import annotations

import pytest

from mentorapp.api.field_edit import FieldEditors, FieldEditRefused
from mentorapp.api.records import STRUCTURAL_FIELDS
from mentorapp.ui.readonly_fields import (
    READ_ONLY_KINDS,
    READ_ONLY_RENDERING,
    EditableField,
    PermissionBlock,
    ReadOnlyField,
    classify_read_only,
    computed_explanation,
    edit_form_disposition,
    field_edit_reason,
    permission_explanation,
    system_explanation,
)

HOURS_BLOCK = PermissionBlock("Edit mentor rates", "your program administrator")

SCHEMA_FIELDS = (
    {"fieldName": "mentorName", "fieldLabel": "Name", "visibilityHints": None},
    {"fieldName": "mentorPhone", "fieldLabel": "Phone", "visibilityHints": {"order": 2}},
    {
        "fieldName": "mentorSessionCount",
        "fieldLabel": "Sessions held",
        "visibilityHints": {"computed": True},
    },
    {"fieldName": "mentorRate", "fieldLabel": "Rate", "visibilityHints": None},
)


# --- The rendering contract (REQ-039) ---------------------------------------------


def test_read_only_fields_render_in_place_and_click_explains() -> None:
    assert READ_ONLY_RENDERING.position == "inPlace"
    # The read view's formatted value — never a grayed-out editor control.
    assert READ_ONLY_RENDERING.value == "readValue"
    assert READ_ONLY_RENDERING.click == "explain"
    # REQ-038: Tab stops only on editable fields.
    assert READ_ONLY_RENDERING.tab_stop is False


def test_the_three_req_039_kinds_are_the_vocabulary() -> None:
    assert READ_ONLY_KINDS == ("computed", "system", "permission")


# --- Classification ----------------------------------------------------------------


def test_ordinary_fields_get_their_editor() -> None:
    assert classify_read_only("mentor", "mentorName", "Name") is None


@pytest.mark.parametrize("field_name", sorted(STRUCTURAL_FIELDS))
def test_every_write_engine_read_only_field_renders_system(field_name: str) -> None:
    read_only = classify_read_only("mentor", field_name, field_name)
    assert isinstance(read_only, ReadOnlyField)
    assert read_only.kind == "system"


def test_the_entity_id_renders_system() -> None:
    read_only = classify_read_only("mentor", "mentorID", "Mentor ID")
    assert read_only is not None
    assert read_only.kind == "system"


def test_computed_is_declared_on_the_registry_rows_visibility_hints() -> None:
    read_only = classify_read_only(
        "mentor", "mentorSessionCount", "Sessions held", visibility_hints={"computed": True}
    )
    assert read_only is not None
    assert read_only.kind == "computed"
    # Other hints alone don't make a field computed.
    hinted = classify_read_only("mentor", "mentorName", "Name", visibility_hints={"order": 1})
    assert hinted is None


def test_a_permission_block_renders_permission_read_only() -> None:
    read_only = classify_read_only("mentor", "mentorRate", "Rate", permission_block=HOURS_BLOCK)
    assert read_only is not None
    assert read_only.kind == "permission"


def test_computed_outranks_permission_and_system_outranks_both() -> None:
    # "No one can edit this" is the truer answer than "you can't".
    computed = classify_read_only(
        "mentor",
        "mentorSessionCount",
        "Sessions held",
        visibility_hints={"computed": True},
        permission_block=HOURS_BLOCK,
    )
    assert computed is not None and computed.kind == "computed"
    system = classify_read_only(
        "mentor",
        "rowVersion",
        "Row version",
        visibility_hints={"computed": True},
        permission_block=HOURS_BLOCK,
    )
    assert system is not None and system.kind == "system"


# --- The explanations (educate voice: what happened → why → what next) -------------


def test_every_explanation_carries_all_three_educate_parts() -> None:
    for message in (
        computed_explanation("Sessions held"),
        system_explanation("Modified at"),
        permission_explanation("Rate", HOURS_BLOCK),
    ):
        assert message.what_happened
        assert message.why
        assert message.what_next
        payload = message.as_payload()
        assert set(payload) == {"whatHappened", "why", "whatNext"}


def test_permission_explanation_names_the_grant_and_the_grantor() -> None:
    message = permission_explanation("Rate", HOURS_BLOCK)
    assert "Edit mentor rates" in message.why
    assert "your program administrator" in message.what_next


def test_computed_explanation_points_at_the_source_fields() -> None:
    message = computed_explanation("Sessions held")
    assert "calculated" in message.why
    assert "Sessions held" in message.what_happened


# --- One explanation, both gestures (REQ-035 bridge) --------------------------------


def test_system_why_matches_field_edits_structural_refusal_verbatim() -> None:
    # api.field_edit refuses structural fields on its own text; the edit form's
    # click must speak the same sentence. Pinned through the public API.
    refusal = FieldEditors().open(
        "w-1", "mentor", "modifiedAt", {"mentorID": "m-1", "rowVersion": 3}
    )
    assert isinstance(refusal, FieldEditRefused)
    assert system_explanation("Modified at").why == refusal.reason


def test_field_edit_reason_hands_the_same_words_to_the_double_click_path() -> None:
    read_only = classify_read_only("mentor", "mentorRate", "Rate", permission_block=HOURS_BLOCK)
    assert read_only is not None
    reason = field_edit_reason(read_only)
    assert read_only.explanation.why in reason
    assert read_only.explanation.what_next in reason
    refusal = FieldEditors().open(
        "w-2",
        "mentor",
        "mentorRate",
        {"mentorID": "m-1", "rowVersion": 3},
        read_only_reason=reason,
    )
    assert isinstance(refusal, FieldEditRefused)
    assert refusal.reason == reason


# --- The edit form's disposition (every field appears, in order) --------------------


def test_disposition_keeps_every_field_in_its_usual_position() -> None:
    dispositions = edit_form_disposition(
        "mentor", SCHEMA_FIELDS, permission_blocks={"mentorRate": HOURS_BLOCK}
    )
    assert [d.field_name for d in dispositions] == [
        "mentorName",
        "mentorPhone",
        "mentorSessionCount",
        "mentorRate",
    ]
    name, phone, sessions, rate = dispositions
    assert isinstance(name, EditableField)
    assert isinstance(phone, EditableField)
    assert isinstance(sessions, ReadOnlyField) and sessions.kind == "computed"
    assert isinstance(rate, ReadOnlyField) and rate.kind == "permission"
    # Read-only fields carry the standard rendering — never dropped, never hidden.
    assert sessions.rendering == READ_ONLY_RENDERING


def test_disposition_without_blocks_is_all_editable_except_computed() -> None:
    dispositions = edit_form_disposition("mentor", SCHEMA_FIELDS)
    read_only = [d for d in dispositions if isinstance(d, ReadOnlyField)]
    assert [d.field_name for d in read_only] == ["mentorSessionCount"]
