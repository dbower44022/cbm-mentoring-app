"""Help-mapping and help-settings entities (WTK-099, REQ-043).

The help-system standard (SKL-116): help CONTENT lives outside the app on the
organization's documentation platform; the app only LINKS to it. These tables
are that link's admin-configured data — no help text is ever stored here.

``helpMapping`` is one page → URL mapping: an administrator points one app
surface (a panel, a data set, or a workprocess — ``sourceType`` names which
kind, ``sourceIdentifier`` names the one instance) at the docs-platform URL
that helps with it. Every Help affordance in the app resolves through this
ONE mapping (REQ-043): the floating Help icon, the menus' last-item Help, and
the workprocess frame's per-step Help all ask the same question — "what URL
helps with the surface the user is on?".

``helpSettings`` is the singleton fallback document the resolution walks when
no mapping row answers:

- ``defaultURLPattern`` — a URL template over the mapping's own coordinates,
  e.g. ``https://docs.example.org/help/{sourceType}/{sourceIdentifier}``.
  Exactly two placeholders, ``{sourceType}`` and ``{sourceIdentifier}``,
  because those coordinates are all a mapping row itself carries: a docs
  platform organized by them gets sensible page-specific URLs for every
  unmapped surface automatically, and any page needing more than its
  coordinates gets an explicit mapping row instead of a richer template
  language. Substitution URL-encodes each value (identifiers may carry
  spaces — workprocess names are display names).
- ``helpHomeURL`` — the help site's front door, the last resort when no
  pattern is configured: an unmapped page opens the home with an
  educate-voice note, never a dead link (REQ-043).

Both settings values default to the empty string, and empty means "not
configured": the row itself is SEEDED by migration 0013 so it exists from
first boot — an unconfigured help system is a normal admin state the resolve
answer explains, not a broken deployment, and the settings PATCH always has
a row (and a ``rowVersion``) to address (DB-S4).

These are platform tables (``StructuralColumnsMixin`` + ``Base``, not
``BaseEntity``): like ``grid`` and ``workprocessRegistration`` they are app
configuration, get no schema-registry rows and no generated read views.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import Index, String, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column, validates

from mentorapp.storage.base import Base, StructuralColumnsMixin, uuid7

# Same partial live-row predicate as workprocess.py (DB-S3, REQ-052).
_LIVE = text('"deletedAt" IS NULL')

# REQ-043's mappable surface kinds. App-validated vocabulary, never a database
# enum (DB-S7): a panel is a navigation destination (its ``panelKey``), a data
# set is a grid's content identity (its ``dataSourceKey``), a workprocess is a
# registered multi-step app (its display name — the action-list identity the
# workprocess standard pins).
HELP_SOURCE_TYPES: Final[tuple[str, ...]] = ("panel", "dataSet", "workprocess")

# The two placeholders ``defaultURLPattern`` may carry — the mapping's own
# coordinates, nothing more (see the module docstring for WHY).
PATTERN_SOURCE_TYPE_PLACEHOLDER: Final = "{sourceType}"
PATTERN_SOURCE_IDENTIFIER_PLACEHOLDER: Final = "{sourceIdentifier}"


class HelpMapping(StructuralColumnsMixin, Base):
    """One admin-configured page → help-URL mapping (REQ-043).

    A live row is the whole fact: the surface ``(sourceType,
    sourceIdentifier)`` opens ``helpURL``. Unmapping is a soft delete; the
    partial unique index lets the same surface be re-mapped later (the
    association-table shape). No content columns by design — help lives on
    the docs platform (SKL-116), never in the app.
    """

    __tablename__ = "helpMapping"
    __table_args__ = (
        # One live mapping per surface: the resolve read is a point lookup,
        # and two live answers for one page would make Help nondeterministic.
        Index(
            "uq_helpMapping_source_live",
            "sourceType",
            "sourceIdentifier",
            unique=True,
            sqlite_where=_LIVE,
            postgresql_where=_LIVE,
        ),
    )

    help_mapping_id: Mapped[uuid.UUID] = mapped_column(
        "helpMappingID", primary_key=True, default=uuid7
    )
    source_type: Mapped[str] = mapped_column("sourceType", String(20), nullable=False)
    source_identifier: Mapped[str] = mapped_column(
        "sourceIdentifier", String(200), nullable=False
    )
    help_url: Mapped[str] = mapped_column("helpURL", String(2000), nullable=False)

    @validates("source_type")
    def _reject_unknown_source_type(self, _key: str, value: str) -> str:
        # Persistence-boundary backstop of the DB-S7 app-validated vocabulary
        # (the workprocess @validates precedent): writers that never ride the
        # API surface (seeds, jobs, tests) cannot mint a surface kind the
        # resolve read would never be asked about.
        if value not in HELP_SOURCE_TYPES:
            raise ValueError(
                f"sourceType must be one of {HELP_SOURCE_TYPES}, got {value!r} (REQ-043)."
            )
        return value


class HelpSettings(StructuralColumnsMixin, Base):
    """The singleton help fallback document (REQ-043).

    One row, seeded by migration 0013 and only ever PATCHed: with no
    create-settings endpoint, seed + no-create IS the singleton contract
    (the ``typeScale`` precedent). Empty-string values mean "not configured"
    — see the module docstring for why absence is a row state, not a
    missing row.
    """

    __tablename__ = "helpSettings"

    help_settings_id: Mapped[uuid.UUID] = mapped_column(
        "helpSettingsID", primary_key=True, default=uuid7
    )
    help_home_url: Mapped[str] = mapped_column(
        "helpHomeURL", String(2000), nullable=False, default=""
    )
    default_url_pattern: Mapped[str] = mapped_column(
        "defaultURLPattern", String(2000), nullable=False, default=""
    )


def live_help_mapping(
    session: Session, *, source_type: str, source_identifier: str
) -> HelpMapping | None:
    """The one live mapping for the named surface, or ``None`` — the resolve read.

    Live rows only (DB-S3): unmapping a page changes the very next Help
    click. ``None`` is a normal answer the resolution walks past (pattern,
    then home), never an error.
    """
    return session.scalars(
        select(HelpMapping).where(
            HelpMapping.source_type == source_type,
            HelpMapping.source_identifier == source_identifier,
            HelpMapping.deleted_at.is_(None),
        )
    ).one_or_none()


def help_settings(session: Session) -> HelpSettings:
    """The ONE persisted help-settings row (REQ-043).

    Returns the live seeded singleton — what the resolve fallback and the
    admin settings surface both read. Raises :class:`LookupError` if the
    seed (migration 0013) is absent — a broken deployment, not a normal
    state (the :func:`~mentorapp.storage.theming.shared_type_scale` shape).
    """
    settings = session.scalars(
        select(HelpSettings).where(HelpSettings.deleted_at.is_(None))
    ).one_or_none()
    if settings is None:
        raise LookupError(
            "The help settings singleton is not seeded; run migrations (0013 persists it)."
        )
    return settings
