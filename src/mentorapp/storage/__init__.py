"""Storage layer — base-entity model, key policy, registries, jobs, and change feed.

Implements the ENG-004 data-model standard: ``entity`` carries the declarative
base, structural system columns, and key-naming policy (WTK-125); ``models``
carries the schema registry, option sets, background jobs, and change feed
(WTK-126/WTK-127). One declarative ``Base`` spans them all.
"""

from mentorapp.storage.base import StructuralColumnsMixin, utcnow
from mentorapp.storage.entity import (
    Base,
    BaseEntity,
    entity_key,
    entity_ref,
    live_index,
    live_unique,
)
from mentorapp.storage.ids import uuid7, uuid7_created_at
from mentorapp.storage.models import (
    CHANGE_KINDS,
    JOB_STATUSES,
    SELECTION_CONTRACTS,
    BackgroundJob,
    ChangeFeedEntry,
    DuplicateOverride,
    FieldChange,
    OptionSet,
    OptionValue,
    PostalCode,
    SchemaRegistry,
    UserPreference,
    WorkprocessRegistration,
)

__all__ = [
    "CHANGE_KINDS",
    "JOB_STATUSES",
    "SELECTION_CONTRACTS",
    "BackgroundJob",
    "Base",
    "BaseEntity",
    "ChangeFeedEntry",
    "DuplicateOverride",
    "FieldChange",
    "OptionSet",
    "OptionValue",
    "PostalCode",
    "SchemaRegistry",
    "StructuralColumnsMixin",
    "UserPreference",
    "WorkprocessRegistration",
    "entity_key",
    "entity_ref",
    "live_index",
    "live_unique",
    "utcnow",
    "uuid7",
    "uuid7_created_at",
]
