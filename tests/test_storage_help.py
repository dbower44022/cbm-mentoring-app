"""Tests for the help-system data model (WTK-107, REQ-043): entity-named
UUIDv7 keys, the source-type vocabulary in its one canonical home, the one
live mapping per surface, the resolve point-read, and the settings singleton
contract (seeded row, empty means unconfigured)."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mentorapp.storage import (
    HELP_SOURCE_TYPES,
    HelpMapping,
    HelpSettings,
    help_settings,
    live_help_mapping,
    utcnow,
    uuid7_created_at,
)


def _mapping(
    session: Session,
    *,
    source_type: str = "panel",
    source_identifier: str = "engagements",
    help_url: str = "https://docs.example.org/help/engagements",
) -> HelpMapping:
    mapping = HelpMapping(
        source_type=source_type,
        source_identifier=source_identifier,
        help_url=help_url,
    )
    session.add(mapping)
    session.flush()
    return mapping


def test_source_type_vocabulary_is_pinned() -> None:
    # REQ-043: the three mappable surface kinds, in their ONE canonical home —
    # the resolve endpoint and the admin CRUD both validate against this.
    assert HELP_SOURCE_TYPES == ("panel", "dataSet", "workprocess")


def test_mapping_gets_entity_named_uuid7_key_and_structural_columns(
    session: Session,
) -> None:
    mapping = _mapping(session)
    # DB-R1/DB-R2: app-generated UUIDv7 under the entity-named key.
    assert mapping.help_mapping_id.version == 7
    assert uuid7_created_at(mapping.help_mapping_id) is not None
    assert HelpMapping.__table__.c["helpMappingID"].primary_key
    # DB-S3/DB-S4: live by default, versioned from 1.
    assert mapping.deleted_at is None
    assert mapping.row_version == 1


def test_mapping_rejects_unknown_source_type_at_the_persistence_boundary(
    session: Session,
) -> None:
    # The DB-S7 backstop: a seed or job can't mint a surface kind the
    # resolve read would never be asked about.
    with pytest.raises(ValueError, match="sourceType"):
        _mapping(session, source_type="dashboard")


def test_one_live_mapping_per_surface_with_remap_after_soft_delete(
    session: Session,
) -> None:
    _mapping(session)
    # A second LIVE row for the same surface would make Help nondeterministic.
    with pytest.raises(IntegrityError):
        _mapping(session, help_url="https://docs.example.org/other")
    # The failed flush rolled the whole in-memory transaction back; rebuild
    # the live row to exercise the soft-delete release.
    session.rollback()
    first = _mapping(session)

    # Unmapping is a soft delete; the partial unique index frees the surface
    # for a fresh mapping the moment the old row died (REQ-052 semantics).
    first.deleted_at = utcnow()
    session.flush()
    remapped = _mapping(session, help_url="https://docs.example.org/v2/engagements")
    assert remapped.help_mapping_id != first.help_mapping_id


def test_live_help_mapping_is_a_live_rows_only_point_read(session: Session) -> None:
    mapping = _mapping(session)
    # Same surface coordinates in a DIFFERENT kind resolve independently —
    # a panel and a data set may share an identifier without colliding.
    _mapping(session, source_type="dataSet", help_url="https://docs.example.org/ds")

    found = live_help_mapping(session, source_type="panel", source_identifier="engagements")
    assert found is not None
    assert found.help_mapping_id == mapping.help_mapping_id

    # None is the normal walk-past answer: unknown surface, or unmapped page.
    assert live_help_mapping(session, source_type="panel", source_identifier="unknown") is None

    mapping.deleted_at = utcnow()
    session.flush()
    assert (
        live_help_mapping(session, source_type="panel", source_identifier="engagements") is None
    )


def test_help_settings_reads_the_seeded_singleton_and_is_loud_when_absent(
    session: Session,
) -> None:
    # No seed (create_all schema, not the migrated chain): a missing singleton
    # is a broken deployment and must be loud, never a silent empty answer.
    with pytest.raises(LookupError, match="0013"):
        help_settings(session)

    row = HelpSettings()
    session.add(row)
    session.flush()
    found = help_settings(session)
    assert found.help_settings_id == row.help_settings_id
    # The seed contract: empty strings mean "not configured yet" — a normal
    # admin state the resolve endpoint explains, not an error.
    assert found.help_home_url == ""
    assert found.default_url_pattern == ""
