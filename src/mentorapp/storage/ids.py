"""UUIDv7 identifiers — the primary-key policy of the data model standard (DB-R1).

Every unique ID in the system is a time-ordered UUIDv7, generated in the app
layer before insert: keys append at the primary-key index edge, and a
record's creation time is recoverable from its ID.
"""

from __future__ import annotations

import secrets
import time
import uuid
from datetime import UTC, datetime

# RFC 9562 UUIDv7 layout: unix_ts_ms(48) | ver(4) | rand_a(12) | var(2) | rand_b(62).
_TS_SHIFT = 80
_TS_MASK = (1 << 48) - 1


def uuid7() -> uuid.UUID:
    """A new RFC 9562 UUIDv7: 48-bit unix-millisecond timestamp + 74 random bits.

    Returns a time-ordered ``uuid.UUID``; never raises.
    """
    # stdlib gains uuid.uuid7 only in Python 3.14; at ~10 lines this is the
    # boring-dependency call (write it, drop it when requires-python moves).
    value = (time.time_ns() // 1_000_000 & _TS_MASK) << _TS_SHIFT
    value |= 0x7 << 76
    value |= secrets.randbits(12) << 64
    value |= 0b10 << 62
    value |= secrets.randbits(62)
    return uuid.UUID(int=value)


def uuid7_created_at(entity_id: uuid.UUID) -> datetime:
    """The creation instant encoded in a UUIDv7 primary key (DB-R1).

    Raises ``ValueError`` if ``entity_id`` is not a version-7 UUID.
    """
    if entity_id.version != 7:
        raise ValueError(f"not a UUIDv7: {entity_id}")
    return datetime.fromtimestamp((entity_id.int >> _TS_SHIFT) / 1000, tz=UTC)
