"""Relationship lookup type-ahead: the suggestion read (REQ-036, WTK-072).

The build of the wiring WTK-060 deferred: :mod:`mentorapp.ui.lookup_control`
fixes what the dropdown SHOWS and :mod:`mentorapp.access.lookup_grants` hands
over every access fact — this module composes the two into the one executable
suggestion read. Per the form_input precedent it is engine surface: the form
screens wire :func:`suggest_related_records` as a thin GET when they land,
and the two inline affordances the control declares are already executable
in the control's own module (``open_linked_record`` hands the value to the
pop-out machinery; ``adopt_created_record`` lands a create pop-out's first
save back in the field).

The composition rules, each with one canonical home reused here:

- **Access decides first.** :func:`~mentorapp.access.lookup_grants.
  stored_lookup_scope` authorizes and scopes in one call; a role denial or
  an unbound entity renders as the control's ``noAccess`` phase — the field
  stays visible and explains, never hides (REQ-036 under the never-hide
  rule). Unbound gets its own educate message: it is a configuration
  defect, not the user's missing role.
- **The query is the grid's search, not a second one.** The predicate is
  ``trigram_search_filter`` over the related entity's registry-declared
  searchable columns, live rows only; ``count_and_aggregates`` computes the
  full-set total the same way the grid's status bar does. The dropdown's
  ``total_matches`` is server-side truth; the window is rendering only.
- **A user-scoped source scopes the suggestions.** A non-null
  ``LookupScope.user_row_filter`` binds the session user on that column,
  exactly as ``execute_admin_sql`` binds ``:currentUserID`` — a lookup may
  never widen what its governing source shows. A filter column the entity
  does not carry fails loudly: quietly unscoped suggestions would be a
  permission hole, not a fallback.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from mentorapp.access.grants import DataSourceAccessError
from mentorapp.access.lookup_grants import (
    LookupSourceResolver,
    LookupUnboundError,
    stored_lookup_scope,
)
from mentorapp.api.list_engine import count_and_aggregates, trigram_search_filter
from mentorapp.api.records import (
    attribute_keys_by_field_name,
    columns_by_field_name,
    primary_key_field,
    record_id_of,
    registry_for,
)
from mentorapp.observability import get_logger
from mentorapp.storage import SchemaRegistry
from mentorapp.ui.grid_panel import search_is_live
from mentorapp.ui.lookup_control import (
    SUGGESTION_WINDOW,
    SuggestionOutcome,
    lookup_unbound_message,
    related_entity_type,
    resolve_suggestions,
)
from mentorapp.ui.record_preview import RecordRef

log = get_logger(__name__)


def suggestion_title(record: Any, registry: dict[str, SchemaRegistry]) -> str:
    """One suggestion row's display title: the searchable text that matched.

    The dropdown shows the values the needle was matched against — the user
    sees exactly why a row is offered, and no per-entity title template has
    to exist before form screens land. A record whose searchable columns are
    all empty falls back to its ID: a suggestion may never render blank.
    """
    attr_keys = attribute_keys_by_field_name(type(record))
    parts: list[str] = []
    for field_name, row in registry.items():
        if not row.searchable_flag or field_name not in attr_keys:
            continue
        value = getattr(record, attr_keys[field_name])
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return " · ".join(parts) if parts else str(record_id_of(record))


def suggest_related_records(
    session: Session,
    resolver: LookupSourceResolver,
    *,
    entity_cls: type[Any],
    field_name: str,
    search_text: str,
    user_id: uuid.UUID,
    user_roles: frozenset[str],
    related_label: str,
) -> SuggestionOutcome:
    """Answer one type-ahead keystroke for a relationship field (REQ-036).

    ``field_name`` is the entity-named FK (``mentorID`` → the ``mentor``
    lookup — a non-reference name raises ``ValueError``, a caller bug);
    ``entity_cls`` is the related entity's declarative class as the record
    catalog resolves it. Always returns a renderable
    :class:`~mentorapp.ui.lookup_control.SuggestionOutcome` for access and
    search states (denied/unbound → ``noAccess``; short text → ``idle`` /
    ``keepTyping``, no query runs). Raises ``ApiValidationError`` when the
    related entity has no searchable columns and ``RuntimeError`` when the
    governing source's row-filter column is missing from the entity — both
    configuration defects, never silently absorbed.
    """
    related = related_entity_type(field_name)
    try:
        scope = stored_lookup_scope(
            session,
            resolver,
            related_entity_type=related,
            user_id=user_id,
            user_roles=user_roles,
        )
    except LookupUnboundError:
        return SuggestionOutcome(
            phase="noAccess", message=lookup_unbound_message(related_label)
        )
    except DataSourceAccessError as denied:
        return resolve_suggestions(
            search_text,
            related_label=related_label,
            data_source_key=denied.data_source_key,
            has_access=False,
        )
    # Below the live threshold the control educates instead of searching —
    # the shared presentation rule decides, and no query is issued.
    if not search_text.strip() or not search_is_live(search_text):
        return resolve_suggestions(
            search_text, related_label=related_label, data_source_key=scope.data_source_key
        )

    filters: list[ColumnElement[bool]] = [
        trigram_search_filter(session, entity_cls, related, search_text)
    ]
    columns = columns_by_field_name(entity_cls)
    if scope.user_row_filter is not None:
        scoping_column = columns.get(scope.user_row_filter)
        if scoping_column is None:
            log.error(
                "lookup row-filter column missing from entity",
                extra={
                    "context": {
                        "relatedEntityType": related,
                        "dataSourceKey": scope.data_source_key,
                        "userRowFilter": scope.user_row_filter,
                    }
                },
            )
            raise RuntimeError(
                f"data source {scope.data_source_key!r} scopes rows on "
                f"{scope.user_row_filter!r}, which {related!r} does not carry"
            )
        filters.append(scoping_column == user_id)

    # Full-set truth first (live rows only, same filters as the window):
    # the dropdown's count must never be the loaded window's length.
    total = int(count_and_aggregates(session, entity_cls, filters=filters)["totalCount"])
    # UUIDv7 keys are time-ordered, so pk order gives a deterministic window
    # that is stable across keystrokes; relevance ranking is a later
    # refinement, deliberately not invented here.
    _, pk_attr = primary_key_field(entity_cls)
    stmt = (
        select(entity_cls)
        .where(*filters, columns["deletedAt"].is_(None))
        .order_by(pk_attr)
        .limit(SUGGESTION_WINDOW)
    )
    registry = registry_for(session, related)
    matches = [
        RecordRef(
            entity_type=related,
            record_id=str(record_id_of(row)),
            title=suggestion_title(row, registry),
        )
        for row in session.scalars(stmt)
    ]
    return resolve_suggestions(
        search_text,
        related_label=related_label,
        data_source_key=scope.data_source_key,
        matches=matches,
        total_matches=total,
    )
