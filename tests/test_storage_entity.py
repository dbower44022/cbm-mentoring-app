"""Base-entity design gate: key policy, system columns, lifecycle (WTK-125)."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.pool import StaticPool

from mentorapp.storage import (
    Base,
    BaseEntity,
    entity_key,
    entity_ref,
    live_unique,
    uuid7,
    uuid7_created_at,
)

SYSTEM_COLUMNS = {
    "createdAt",
    "createdBy",
    "modifiedAt",
    "modifiedBy",
    "deletedAt",
    "deletedBy",
    "rowVersion",
    "customAttributes",
}


class Mentor(BaseEntity):
    __tablename__ = "Mentor"
    __table_args__ = (live_unique("uq_Mentor_mentorEmail_live", "mentorEmail"),)

    mentor_id: Mapped[uuid.UUID] = entity_key("mentorID")
    mentor_email: Mapped[str] = mapped_column("mentorEmail", nullable=False)


class MentorNote(BaseEntity):
    __tablename__ = "MentorNote"

    mentor_note_id: Mapped[uuid.UUID] = entity_key("mentorNoteID")
    mentor_id: Mapped[uuid.UUID] = entity_ref("Mentor.mentorID")


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_uuid7_version_variant_and_time_recovery() -> None:
    entity_id = uuid7()
    assert entity_id.version == 7
    assert entity_id.variant == uuid.RFC_4122
    assert abs(uuid7_created_at(entity_id) - datetime.now(UTC)) < timedelta(seconds=5)
    with pytest.raises(ValueError, match="not a UUIDv7"):
        uuid7_created_at(uuid.uuid4())


def test_uuid7_is_time_ordered() -> None:
    earlier = uuid7()
    time.sleep(0.005)
    later = uuid7()
    assert later.int > earlier.int


def test_entity_named_keys_and_system_columns() -> None:
    assert [c.name for c in Mentor.__table__.primary_key.columns] == ["mentorID"]
    assert SYSTEM_COLUMNS.issubset(Mentor.__table__.columns.keys())
    assert SYSTEM_COLUMNS.issubset(MentorNote.__table__.columns.keys())
    # DB-R2b: the FK carries the identical name as the PK it references.
    fk = next(iter(MentorNote.__table__.columns["mentorID"].foreign_keys))
    assert fk.target_fullname == "Mentor.mentorID"


def test_insert_populates_system_defaults(session: Session) -> None:
    mentor = Mentor(mentor_email="pat@example.org")
    session.add(mentor)
    session.flush()
    assert mentor.mentor_id.version == 7
    assert mentor.row_version == 1
    assert mentor.custom_attributes == {}
    assert mentor.deleted_at is None and mentor.deleted_by is None
    assert abs(mentor.created_at - datetime.now(UTC)) < timedelta(seconds=5)
    assert abs(mentor.modified_at - datetime.now(UTC)) < timedelta(seconds=5)


def test_row_version_increments_on_update(session: Session) -> None:
    mentor = Mentor(mentor_email="pat@example.org")
    session.add(mentor)
    session.commit()
    mentor.mentor_email = "pat.new@example.org"
    session.commit()
    assert mentor.row_version == 2


def test_stale_row_version_write_conflicts(session: Session) -> None:
    mentor = Mentor(mentor_email="pat@example.org")
    session.add(mentor)
    session.commit()
    # An out-of-band writer bumps the version underneath the loaded object;
    # synchronize_session=False keeps the in-memory copy genuinely stale.
    session.execute(
        update(Mentor)
        .where(Mentor.mentor_id == mentor.mentor_id)
        .values(row_version=Mentor.row_version + 1),
        execution_options={"synchronize_session": False},
    )
    mentor.mentor_email = "stale@example.org"
    with pytest.raises(StaleDataError):
        session.commit()


def test_soft_delete_and_restore(session: Session) -> None:
    actor = uuid7()
    mentor = Mentor(mentor_email="pat@example.org")
    session.add(mentor)
    session.commit()
    mentor.soft_delete(deleted_by=actor)
    session.commit()
    assert mentor.is_deleted
    assert mentor.deleted_by == actor
    mentor.restore()
    session.commit()
    assert not mentor.is_deleted
    assert mentor.deleted_by is None


def test_live_unique_ignores_soft_deleted_rows(session: Session) -> None:
    corpse = Mentor(mentor_email="pat@example.org")
    session.add(corpse)
    session.commit()
    corpse.soft_delete()
    session.commit()
    # Re-adding a live duplicate of a deleted row does not collide (DB-S3)...
    session.add(Mentor(mentor_email="pat@example.org"))
    session.commit()
    # ...but a second LIVE duplicate does.
    session.add(Mentor(mentor_email="pat@example.org"))
    with pytest.raises(IntegrityError):
        session.commit()
