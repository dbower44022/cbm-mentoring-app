"""Storage foundation: declarative base, UUIDv7 keys, and the structural columns.

Implements the ENG-004 data-model standard (DB-R1, DB-R2, DB-S3, DB-S4, DB-S5):
UUIDv7 primary keys generated app-side, camelCase database column names with
entity-named keys, and the structural/system columns every entity table carries
(``createdAt``/``createdBy``, ``modifiedAt``/``modifiedBy``, ``deletedAt``/
``deletedBy``, ``rowVersion``, ``customAttributes``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

# WSK-022 merge resolution: the declarative Base and the UUIDv7 primitive live
# in entity.py/ids.py (WTK-125); this module re-exports them so its own
# consumers (models.py) keep one metadata and one key policy.
from mentorapp.storage.entity import Base
from mentorapp.storage.ids import uuid7

# JSONB on Postgres (GIN-indexable per DB-R3); plain JSON on SQLite for tests.
JsonValue = JSON().with_variant(JSONB(), "postgresql")

__all__ = ["Base", "JsonValue", "StructuralColumnsMixin", "utcnow", "uuid7"]




def utcnow() -> datetime:
    """Timezone-aware UTC now — the single timestamp source for audit columns."""
    return datetime.now(UTC)


class StructuralColumnsMixin:
    """The structural/system columns identical on every entity table (DB-R2 exemption).

    Audit columns are API-maintained on every write (DB-S5); ORM defaults here are
    the app-layer implementation of that rule. ``createdBy``/``modifiedBy``/
    ``deletedBy`` are nullable because seed migrations and system jobs write rows
    with no acting user; the API sets them on every user-initiated write.
    """

    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True), nullable=False, default=utcnow
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column("createdBy", default=None)
    # modifiedAt is indexed on every table — it powers the change feed (DB-S5, DB-S10).
    modified_at: Mapped[datetime] = mapped_column(
        "modifiedAt",
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        index=True,
    )
    modified_by: Mapped[uuid.UUID | None] = mapped_column("modifiedBy", default=None)
    # Soft delete (DB-S3): null deletedAt = live row; rows are never physically deleted.
    deleted_at: Mapped[datetime | None] = mapped_column(
        "deletedAt", DateTime(timezone=True), default=None
    )
    deleted_by: Mapped[uuid.UUID | None] = mapped_column("deletedBy", default=None)
    row_version: Mapped[int] = mapped_column("rowVersion", nullable=False, default=1)
    custom_attributes: Mapped[dict[str, Any]] = mapped_column(
        "customAttributes", JsonValue, nullable=False, default=dict
    )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:  # noqa: N805 — SQLAlchemy declarative hook
        # rowVersion increments on every ORM update (DB-S4 optimistic concurrency);
        # a stale-version UPDATE matches zero rows and raises StaleDataError.
        return {"version_id_col": cls.row_version}
