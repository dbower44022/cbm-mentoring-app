"""Prototype-gate delta journeys: entry sizing & rich text (REQ-089/090, WTK-206).

Chained user journeys over the WTK-204/205 delta — not per-fact units
(``test_ui_entry_editors`` owns those): each scenario drives the real
registry seed, the live ``GET /schema/{entity}`` surface, and the UI
controls together, so the acceptance summaries are proven end to end rather
than declaration by declaration. REQ-090: formatted content pasted into a
narrative field travels as clean HTML from the served schema through the
one control into the single-field PATCH, markup intact; the SAME control
resolves at every rich-text entry point the wire schema names. REQ-089: a
prep-surface resize session — restore, drag larger, drag smaller past the
floors, drag back — consumes the panel's height at every stop and never
leaves a fixed-height box above idle space.

MeetingNote is the narrative guinea pig as in ``test_storage_mentoring`` —
the routing is registry-type-driven; nothing here is note-specific.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from mentorapp.api import FieldSettings, validate_on_exit
from mentorapp.api.deps import get_session
from mentorapp.api.field_edit import CommitSingleField, FieldEditors
from mentorapp.main import create_app
from mentorapp.storage import (
    MeetingNote,
    NextStep,
    ProgressGoal,
    SessionLog,
    seed_built_in_registry,
)
from mentorapp.ui.entry_editors import (
    MIN_EDITOR_HEIGHT,
    PREP_ENTRY_EDITORS,
    RICH_TEXT_CONTROL,
    fill_entry_layout,
)
from mentorapp.ui.field_edit_window import OpenFieldWindow, open_from_double_click

NARRATIVE_ENTITIES = (MeetingNote, NextStep, ProgressGoal, SessionLog)
NARRATIVE_FIELDS = {
    "meetingNote": "meetingNoteBody",
    "nextStep": "nextStepDescription",
    "progressGoal": "progressGoalDescription",
    "sessionLog": "sessionLogSummary",
}

# What survives a high-fidelity paste from Word/email (REQ-090's acceptance
# axis): formatting, a list, and a link — as the clean semantic HTML the
# control emits.
PASTED_FROM_WORD = (
    "<p>Agreed <strong>next steps</strong>:</p>"
    "<ul><li>Send the <a href='https://cbm.example/deck'>deck</a></li>"
    "<li><em>Review</em> the financials</li></ul>"
)


@pytest.fixture()
def client(session: Session) -> TestClient:
    seed_built_in_registry(session, NARRATIVE_ENTITIES)
    session.flush()
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def _served_field(client: TestClient, entity_type: str, field_name: str) -> dict[str, Any]:
    """One field's ``GET /schema/{entity}`` entry, verbatim off the wire."""
    body = client.get(f"/schema/{entity_type}").json()
    assert body["errors"] is None
    matches = [f for f in body["data"]["fields"] if f["fieldName"] == field_name]
    assert len(matches) == 1
    return matches[0]


# --- REQ-090: paste-to-PATCH, driven by the served schema ---------------------------


def test_pasted_word_content_travels_as_clean_html_from_schema_to_patch(
    client: TestClient,
) -> None:
    # The chain the acceptance summary describes: the seeded registry serves
    # the narrative field as richText; the double-click bridge routes that
    # served type to THE rich-text control; the pasted formatting, list, and
    # link pass settings-driven validation and land in the single-field
    # PATCH byte for byte — no step downgrades or strips the markup.
    spec = _served_field(client, "meetingNote", "meetingNoteBody")
    assert spec["fieldType"] == "richText"

    record: dict[str, Any] = {
        "meetingNoteID": "0197-note-1",
        "meetingNoteBody": "<p>First draft.</p>",
        "rowVersion": 3,
        "modifiedAt": "2026-07-05T00:00:03Z",
        "modifiedBy": "someone-else",
    }
    editors = FieldEditors()
    window = open_from_double_click(editors, "w1", "meetingNote", spec, record)
    assert isinstance(window, OpenFieldWindow)
    assert window.control is RICH_TEXT_CONTROL
    # The control's paste contract covers the sources the requirement names.
    assert {"word", "email"} <= set(RICH_TEXT_CONTROL.paste_sources)

    # Validation reads the SAME wire payload the schema served — clean HTML
    # is a valid richText value by settings, never by a per-form rule.
    assert validate_on_exit(FieldSettings.from_wire(spec), PASTED_FROM_WORD) is None

    editors.edit_value("w1", PASTED_FROM_WORD)
    outcome = editors.request_save("w1")
    assert isinstance(outcome, CommitSingleField)
    assert outcome.payload == {"meetingNoteBody": PASTED_FROM_WORD, "rowVersion": 3}
    # Formatting, list, and link survived the whole journey intact.
    for markup in ("<strong>", "<ul>", "<a href="):
        assert markup in outcome.payload["meetingNoteBody"]


def test_every_served_rich_text_entry_point_resolves_to_the_one_control(
    client: TestClient,
) -> None:
    # "The same control appears at every rich-text entry point": every
    # narrative field the wire schema serves opens on the identical control
    # object — and the prep surface's editors host it too, so there is no
    # entry point left to drift.
    for entity_type, field_name in NARRATIVE_FIELDS.items():
        spec = _served_field(client, entity_type, field_name)
        assert spec["fieldType"] == "richText"
        record = {
            field_name: "<p>existing</p>",
            "rowVersion": 1,
            "modifiedAt": "2026-07-05T00:00:01Z",
            "modifiedBy": "someone-else",
            f"{entity_type}ID": "0197-rec-1",
        }
        window = open_from_double_click(FieldEditors(), "w1", entity_type, spec, record)
        assert isinstance(window, OpenFieldWindow)
        assert window.control is RICH_TEXT_CONTROL
    for editor in PREP_ENTRY_EDITORS:
        assert editor.control == RICH_TEXT_CONTROL


# --- REQ-089: the resize session -----------------------------------------------------


def test_resize_session_consumes_the_panel_at_every_stop() -> None:
    # One sitting on the prep surface: the panel opens at its persisted
    # REQ-087 height, then the user drags the splitter wider twice and the
    # window larger once. Every stop re-fills exactly; every enlargement
    # visibly enlarges BOTH editors — never a fixed-height box over blanks.
    chrome = 132  # heading + toolbar + save row + assist card, flex-none
    stops = (560, 720, 880, 1200)
    previous = None
    for height in stops:
        layout = fill_entry_layout(height, PREP_ENTRY_EDITORS, fixed_chrome_px=chrome)
        assert not layout.panel_scrolls
        assert sum(layout.allocations.values()) == height - chrome
        if previous is not None:
            for editor in PREP_ENTRY_EDITORS:
                assert layout.height_of(editor.key) > previous.height_of(editor.key)
        previous = layout

    # REQ-087 composes: reopening on the persisted height reproduces the
    # sitting's first layout exactly — the allocator is pure in the height.
    restored = fill_entry_layout(stops[0], PREP_ENTRY_EDITORS, fixed_chrome_px=chrome)
    first = fill_entry_layout(stops[0], PREP_ENTRY_EDITORS, fixed_chrome_px=chrome)
    assert restored.allocations == first.allocations


def test_shrink_past_the_floors_scrolls_then_recovers() -> None:
    # The same sitting, downward: dragging under the readability floors puts
    # the editors AT their floors with the panel scrolling — no unreadable
    # squash — and dragging back up resumes exact fill with no dead band.
    squeezed = fill_entry_layout(150, PREP_ENTRY_EDITORS, fixed_chrome_px=40)
    assert squeezed.panel_scrolls
    for editor in PREP_ENTRY_EDITORS:
        assert squeezed.height_of(editor.key) == MIN_EDITOR_HEIGHT

    recovered = fill_entry_layout(700, PREP_ENTRY_EDITORS, fixed_chrome_px=40)
    assert not recovered.panel_scrolls
    assert sum(recovered.allocations.values()) == 700 - 40


def test_any_panel_size_fills_or_scrolls_never_idles() -> None:
    # "At any panel size": sweep the drag range pixel-family by pixel-family.
    # Above the floors every pixel is allocated; below them the floors hold;
    # growing never shrinks an editor anywhere in the range — so no height
    # exists at which blank panel space sits under the editors.
    floor_sum = sum(e.min_height_px for e in PREP_ENTRY_EDITORS)
    previous = None
    for height in range(0, 1601, 7):
        layout = fill_entry_layout(height, PREP_ENTRY_EDITORS)
        if height < floor_sum:
            assert layout.panel_scrolls
        else:
            assert not layout.panel_scrolls
            assert sum(layout.allocations.values()) == height
        if previous is not None:
            for editor in PREP_ENTRY_EDITORS:
                assert layout.height_of(editor.key) >= previous.height_of(editor.key)
        previous = layout
