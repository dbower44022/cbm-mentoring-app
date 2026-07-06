"""The base-entity model: system columns, lifecycle, and the key-naming policy.

Implements the data-model standard (REQ-047, REQ-048, REQ-049, REQ-052,
REQ-053, REQ-054): entity-named UUIDv7 primary keys, the eight structural
system columns exempt from entity-naming, soft delete, optimistic
concurrency via ``rowVersion``, audit columns, and the ``customAttributes``
JSONB bag. Every entity table subclasses :class:`BaseEntity`.

Database column names are camelCase per the approved standard; Python
attributes stay snake_case so the code follows PEP 8 while the schema
follows the naming rule.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar, Final

from sqlalchemy import DateTime, ForeignKey, Index, MetaData, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedColumn, declared_attr, mapped_column
from sqlalchemy.types import JSON

from mentorapp.storage.ids import uuid7

# Deterministic constraint names keep the dual Alembic heads (Postgres +
# SQLite batch mode) stable and diffable.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# JSONB on Postgres (the production dialect, GIN-indexable per DB-R3);
# generic JSON elsewhere so the model suite runs on in-memory SQLite.
JsonObject = JSON().with_variant(JSONB(), "postgresql")

# Live-row predicate for partial indexes/constraints (DB-S3). Double quotes
# are identifier quoting on both Postgres and SQLite — camelCase needs them.
_LIVE_ROWS = text('"deletedAt" IS NULL')

# REQ-063: which side of the REQ-062 ownership boundary masters a data set.
# "application" — this store is the system of record; "crm" — the CRM is, and
# the app table only anchors/references that truth (never a fork of it).
OWNERSHIP_SIDES: Final[tuple[str, ...]] = ("application", "crm")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for every mentorapp table."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012 — SQLAlchemy consumes this declaratively
        uuid.UUID: Uuid(),
        datetime: DateTime(timezone=True),
        dict[str, Any]: JsonObject,
    }


def entity_key(column_name: str) -> MappedColumn[uuid.UUID]:
    """The entity-named UUIDv7 primary key (DB-R1, DB-R2b) — e.g. ``mentorID``.

    App-layer generated before insert; never an auto-increment integer.
    """
    return mapped_column(column_name, primary_key=True, default=uuid7)


def entity_ref(
    target: str, *, nullable: bool = False, registry: dict[str, Any] | None = None
) -> MappedColumn[uuid.UUID]:
    """A foreign key to ``"Table.columnID"`` carrying the identical column name.

    The name is derived from the referenced key, so DB-R2b (FK named exactly
    as the PK it references) holds mechanically, not by convention.
    ``registry`` is the column-site registry metadata, exactly as
    ``mapped_column(info={"registry": ...})`` declares it.
    """
    column_name = target.rsplit(".", maxsplit=1)[-1]
    return mapped_column(
        column_name,
        ForeignKey(target),
        nullable=nullable,
        info={"registry": registry} if registry is not None else {},
    )


def live_index(name: str, *expressions: Any, unique: bool = False) -> Index:
    """An index over live rows only: partial ``WHERE deletedAt IS NULL`` (DB-S3).

    Live-row reads never pay for deleted rows, and a live re-add never
    collides with a soft-deleted corpse.
    """
    return Index(
        name, *expressions, unique=unique, postgresql_where=_LIVE_ROWS, sqlite_where=_LIVE_ROWS
    )


def live_unique(name: str, *expressions: Any) -> Index:
    """A unique constraint scoped to live rows (DB-S3 partial-uniqueness rule)."""
    return live_index(name, *expressions, unique=True)


class BaseEntity(Base):
    """Abstract base carrying the eight structural system columns.

    These columns are identical on every table and exempt from the
    entity-naming rule (DB-R2 exemption): ``createdAt``, ``createdBy``,
    ``modifiedAt``, ``modifiedBy``, ``deletedAt``, ``deletedBy``,
    ``rowVersion``, ``customAttributes``.
    """

    __abstract__ = True

    # REQ-063's ownership-side declaration: every entity states, at design
    # time in source, which side of the REQ-062 boundary owns its data set.
    # Mentoring-process entities take the "application" default; CRM anchor
    # entities must override with "crm".
    __ownership_side__: ClassVar[str] = "application"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        # Validated before SQLAlchemy maps the class, so an invalid or cleared
        # declaration can never produce a mapped table (REQ-063 enforcement).
        side = getattr(cls, "__ownership_side__", None)
        if side not in OWNERSHIP_SIDES:
            raise TypeError(
                f"{cls.__name__} declares ownership side {side!r}; "
                f"REQ-063 requires one of {OWNERSHIP_SIDES}"
            )
        super().__init_subclass__(**kwargs)

    created_at: Mapped[datetime] = mapped_column("createdAt", nullable=False, default=_utcnow)
    # *By columns are nullable: system-originated writes (seeds, migrations,
    # jobs) may predate a user identity; the API stamps them on every
    # user-driven write (DB-S5).
    created_by: Mapped[uuid.UUID | None] = mapped_column("createdBy")
    # modifiedAt powers the change feed, so its index is deliberately NOT
    # partial: soft deletes/restores must surface as feed entries (DB-S10
    # overrides the DB-S3 partial-index rule for this one index).
    modified_at: Mapped[datetime] = mapped_column(
        "modifiedAt", nullable=False, default=_utcnow, onupdate=_utcnow, index=True
    )
    modified_by: Mapped[uuid.UUID | None] = mapped_column("modifiedBy")
    deleted_at: Mapped[datetime | None] = mapped_column("deletedAt")
    deleted_by: Mapped[uuid.UUID | None] = mapped_column("deletedBy")
    row_version: Mapped[int] = mapped_column("rowVersion", nullable=False, default=1)
    custom_attributes: Mapped[dict[str, Any]] = mapped_column(
        "customAttributes", nullable=False, default=dict
    )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:  # noqa: N805 — declarative directive
        # rowVersion is SQLAlchemy's version counter (DB-S4): incremented on
        # every UPDATE, StaleDataError when a stale version writes 0 rows.
        # The API maps that to 409-with-current-record.
        return {"version_id_col": cls.row_version}

    @property
    def is_deleted(self) -> bool:
        """True when the record is soft-deleted (``deletedAt`` set — DB-S3)."""
        return self.deleted_at is not None

    def soft_delete(self, deleted_by: uuid.UUID | None = None) -> None:
        """Stamp ``deletedAt``/``deletedBy``; rows are never physically deleted."""
        self.deleted_at = _utcnow()
        self.deleted_by = deleted_by

    def restore(self) -> None:
        """Clear the soft-delete stamps, returning the record to the live set."""
        self.deleted_at = None
        self.deleted_by = None
