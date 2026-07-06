"""Relationship lookup control design: type-ahead over the related data set (WTK-060).

The UI-layer design for REQ-036. No frontend form screens exist yet, so —
like ``record_preview`` and ``grid_panel`` — this is executable surface the
shell renders verbatim:

- **One control per reference field, registry-driven.** Every field the
  schema registry types as :data:`LOOKUP_FIELD_TYPE` renders as
  :data:`LOOKUP_CONTROL` — never a per-form widget choice. The related
  entity is not extra configuration: :func:`related_entity_type` derives it
  from the entity-named key the field already carries (``mentorID`` →
  ``mentor``), the same DB-R2/R2b naming invariant the registry seed keys on.
- **Type-ahead speaks the app's one search vocabulary.** Liveness is the
  grid's :func:`~mentorapp.ui.grid_panel.search_is_live` (REQ-020's
  3rd-character rule) — the app has exactly one answer to "when does typed
  text search". The suggestion query itself is the read surface the grid
  already uses: ``authorize_data_source`` on the related source, then
  ``trigram_search_filter`` over the related entity's searchable columns,
  live rows only. Endpoint wiring is deferred to the form-screen tasks
  (the ``form_input`` precedent); :func:`resolve_suggestions` fixes what
  the shell shows for whatever the server returned.
- **Server-side truth in the dropdown.** ``total_matches`` is the full
  filtered count, never the suggestion window; :func:`matches_summary`
  says so out loud ("37 matches — showing the first 8").
- **Two inline affordances, never hidden.** :data:`OPEN_LINKED_RECORD`
  (single, safe) hands the field's value straight to the ``record_preview``
  pop-out machinery — pinned real window, raise-if-already-open.
  :data:`CREATE_RELATED_RECORD` (none, modifying) opens the standard create
  form for the related entity in a pop-out; its first save lands back in the
  field via :func:`adopt_created_record` — the user never leaves the form.
  Both are always visible; an invalid invocation explains
  (:func:`nothing_linked_message`), and a user without related-set access
  still sees the control — typing gets :func:`lookup_no_access_message`,
  not a hidden or grayed field.
- **The value is a :class:`~mentorapp.ui.record_preview.RecordRef`.** The
  form writes only ``record_id`` (the FK); the title is display-only and
  may go stale between windows, exactly as the preview's stance.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from mentorapp.api.grid_surface import MIN_SEARCH_LENGTH
from mentorapp.ui.auth_flows import EducateMessage
from mentorapp.ui.grid_panel import search_is_live
from mentorapp.ui.record_preview import PanelAction, RecordRef

# The registry's derived type for FK and soft-reference columns
# (registry_seed._derived_field_type) — the control keys on the registry's
# vocabulary, never on a parallel UI-side type list.
LOOKUP_FIELD_TYPE: Final = "reference"

# Dropdown window only — rendering, never truth. Eight rows keeps the
# dropdown scannable without scrolling on the standard row heights; the
# summary always carries the real total.
SUGGESTION_WINDOW: Final = 8


def related_entity_type(field_name: str) -> str:
    """Derive the related entity from an entity-named key (DB-R2/R2b).

    ``mentorID`` → ``mentor``; ``crmEngagementRefID`` → ``crmEngagementRef``.
    Name equality with the referenced PK is the schema's invariant, not a
    heuristic — the registry seed enforces it, so the control needs no
    separate related-entity setting. A name without the ``ID`` suffix is a
    caller bug (the field is not a reference), reported loudly.
    """
    if len(field_name) <= 2 or not field_name.endswith("ID"):
        raise ValueError(f"not an entity-named reference field: {field_name!r}")
    return field_name[:-2]


@dataclass(frozen=True)
class LookupControl:
    """The one relationship-field control (REQ-036), registry-driven."""

    field_type: str = LOOKUP_FIELD_TYPE
    # REQ-020's threshold, reused: one app-wide answer to "when is it live".
    min_search_length: int = MIN_SEARCH_LENGTH
    suggestion_window: int = SUGGESTION_WINDOW
    affordances: tuple[str, ...] = ("OpenLinkedRecord", "CreateRelatedRecord")
    # The create pop-out's first save becomes the field's value in place.
    create_adopts_new_record: bool = True
    # The form's payload carries the FK only; the title never round-trips.
    value_written: str = "recordID"


LOOKUP_CONTROL = LookupControl()


# Both ride the grid standard's declaration vocabulary so the one
# invalid-invocation explainer serves form affordances too. "Open" acts on
# the single linked record; "New…" needs nothing selected and is modifying
# (it creates a record) — never destructive, so no confirmation.
OPEN_LINKED_RECORD = PanelAction(
    key="OpenLinkedRecord",
    label="Open",
    selection_contract="single",
    classification="safe",
)
CREATE_RELATED_RECORD = PanelAction(
    key="CreateRelatedRecord",
    label="New…",
    selection_contract="none",
    classification="modifying",
)


# The dropdown's whole state vocabulary — the shell renders phase, never
# invents intermediate states.
SUGGESTION_PHASES: Final[tuple[str, ...]] = (
    "idle",
    "keepTyping",
    "matches",
    "noMatches",
    "noAccess",
)


@dataclass(frozen=True)
class SuggestionOutcome:
    """What the dropdown shows after one keystroke.

    ``suggestions`` is the rendered window; ``total_matches`` is the
    server's full filtered count (server-side truth). Exactly one of
    ``summary`` / ``message`` is set outside ``idle`` — a count line for
    matches, an educate message for everything else.
    """

    phase: str
    suggestions: tuple[RecordRef, ...] = ()
    total_matches: int = 0
    summary: str | None = None
    message: EducateMessage | None = None

    def __post_init__(self) -> None:
        if self.phase not in SUGGESTION_PHASES:
            raise ValueError(f"unknown suggestion phase: {self.phase!r}")


def keep_typing_message(related_label: str) -> EducateMessage:
    return EducateMessage(
        what_happened="Search hasn't started yet.",
        why=(
            f"{related_label} lookup searches once at least {MIN_SEARCH_LENGTH} "
            "characters are typed — the app-wide live-search rule."
        ),
        what_next="Keep typing to see matching records.",
    )


def no_matches_message(search_text: str, related_label: str) -> EducateMessage:
    """The zero-match state names create-new — a lookup is never a dead end."""
    return EducateMessage(
        what_happened=f"No {related_label} records match '{search_text}'.",
        why="The search covers the related records you have access to.",
        what_next=(
            f"Check the spelling, or use 'New…' to create the {related_label} record here."
        ),
    )


def lookup_no_access_message(related_label: str, data_source_key: str) -> EducateMessage:
    """The never-hide explainer for a lookup over a source the user can't read."""
    return EducateMessage(
        what_happened=f"{related_label} records can't be searched.",
        why=f"Your roles don't include access to the '{data_source_key}' data source.",
        what_next="Ask an administrator to grant your role access to that data source.",
    )


def nothing_linked_message(field_label: str) -> EducateMessage:
    """The never-hide explainer for Open on a field with no record linked."""
    return EducateMessage(
        what_happened=f"There is no {field_label} record to open.",
        why="'Open' shows the linked record, and this field is empty.",
        what_next=f"Pick a {field_label} record first — or use 'New…' to create one.",
    )


def matches_summary(total_matches: int, shown: int) -> str:
    """The count line under the suggestions — always the full-set truth."""
    if total_matches <= shown:
        return f"{total_matches} matches" if total_matches != 1 else "1 match"
    return f"{total_matches} matches — showing the first {shown}; keep typing to narrow."


def resolve_suggestions(
    search_text: str,
    *,
    related_label: str,
    data_source_key: str,
    has_access: bool = True,
    matches: Sequence[RecordRef] = (),
    total_matches: int | None = None,
) -> SuggestionOutcome:
    """Fix the dropdown for one keystroke — the reference behavior (REQ-036).

    ``matches``/``total_matches`` are what the suggestion endpoint returned
    (already permission-filtered and counted server-side); this function only
    decides presentation. ``has_access`` False models the endpoint's denial:
    the control stays rendered and explains, never hides.
    """
    if not has_access:
        return SuggestionOutcome(
            phase="noAccess",
            message=lookup_no_access_message(related_label, data_source_key),
        )
    if not search_text.strip():
        return SuggestionOutcome(phase="idle")
    if not search_is_live(search_text):
        return SuggestionOutcome(phase="keepTyping", message=keep_typing_message(related_label))
    if not matches:
        return SuggestionOutcome(
            phase="noMatches",
            message=no_matches_message(search_text.strip(), related_label),
        )
    total = len(matches) if total_matches is None else total_matches
    if total < len(matches):
        raise ValueError(f"total_matches {total} below the {len(matches)} returned matches")
    window = tuple(matches[:SUGGESTION_WINDOW])
    return SuggestionOutcome(
        phase="matches",
        suggestions=window,
        total_matches=total,
        summary=matches_summary(total, len(window)),
    )


class NothingLinkedError(LookupError):
    """Open invoked on an empty lookup field; carries the educate message."""

    def __init__(self, field_label: str) -> None:
        super().__init__(f"no record linked in {field_label!r}")
        self.message = nothing_linked_message(field_label)


def open_linked_record(value: RecordRef | None, *, field_label: str) -> RecordRef:
    """The Open affordance: hand the linked record to the pop-out machinery.

    The returned ref goes straight to ``RecordWindows.pop_out`` — pinned real
    window, raise-if-already-open, standard header minus navigation — the
    lookup adds no window behavior of its own. An empty field raises
    :class:`NothingLinkedError` with the explainer (never a no-op click).
    """
    if value is None:
        raise NothingLinkedError(field_label)
    return value


def adopt_created_record(created: RecordRef, *, field_name: str) -> RecordRef:
    """Land a create-pop-out's first save back in the field (REQ-036).

    The adopted ref becomes the control's value in place — the host form
    stays open and still saves normally. The entity check is a wiring guard:
    a create window for the wrong entity must fail loudly, never silently
    write a foreign key of the wrong type.
    """
    expected = related_entity_type(field_name)
    if created.entity_type != expected:
        raise ValueError(
            f"created a {created.entity_type!r} record, but {field_name!r} links {expected!r}"
        )
    return created
