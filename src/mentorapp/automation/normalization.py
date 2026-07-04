"""Shared normalization services (REQ-061, DB-S13): ONE definition of "equal".

The canonical home for every value-normalization concern the platform shares:

- :func:`normalize_for_match` — the single definition of duplicate-match
  equality per field type. The write engine's validation and duplicate
  detection (REQ-053/059), the indexed ``<fieldName>Normalized`` shadow
  columns, and every background job that writes matchable values MUST all go
  through this function — two definitions of "equal" is the bug class this
  module exists to prevent.
- :func:`normalize_phone` / :func:`parse_person_name` /
  :func:`parse_street_address` — the parsing primitives feeding form
  auto-fill and the match columns.
- :func:`normalize_postal_code` / :func:`postal_lookup` — the postal → city/
  state reference read (REQ-061), against rows the refresh job maintains.
- :func:`normalized_shadow_values` — computes every shadow-column value a
  write must persist, from the same registry-declared match rules the
  duplicate detector reads.

Layering: storage → automation → api. This module imports storage only, so
both the write engine and the background workers compose it without cycles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.storage import PostalCode, SchemaRegistry

# US ZIP with optional +4; the lookup table keys on the 5-digit prefix.
_ZIP_PLUS_FOUR = re.compile(r"^(\d{5})(?:-\d{4})?$")
# Trailing "ST 62704[-1234]" (state + ZIP) of a single-line US address.
_STATE_ZIP = re.compile(r"^(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$")


def normalize_phone(value: str) -> str:
    """Digits-only phone normalization — formatting and punctuation never match."""
    return "".join(character for character in value if character.isdigit())


def normalize_for_match(field_type: str, value: Any) -> str:
    """The ONE definition of duplicate-match equality per field type (DB-S13).

    Phones compare digits-only; everything else compares case-insensitively,
    whitespace-trimmed. The normalized shadow columns entities index for
    detection MUST be populated by this same function.
    """
    text = str(value)
    if field_type == "phone":
        return normalize_phone(text)
    return text.strip().lower()


@dataclass(frozen=True, slots=True)
class ParsedName:
    """A person name split for the name-based match columns."""

    first_name: str
    last_name: str


def parse_person_name(value: str) -> ParsedName:
    """Split a display name into (first, last) for name-rule match columns.

    Accepts "Last, First" and "First [Middle …] Last"; middle tokens fold into
    the first name so the last-name column stays a single match key. Single
    tokens are a last name only — surname is the stronger duplicate signal.
    """
    text = " ".join(value.split())
    if "," in text:
        last, _, first = (part.strip() for part in text.partition(","))
        return ParsedName(first_name=first, last_name=last)
    tokens = text.split(" ")
    if len(tokens) < 2 or not tokens[0]:
        return ParsedName(first_name="", last_name=text)
    return ParsedName(first_name=" ".join(tokens[:-1]), last_name=tokens[-1])


@dataclass(frozen=True, slots=True)
class ParsedAddress:
    """A single-line US address split for auto-fill and address match columns."""

    street_line: str
    city_name: str
    state_code: str
    postal_code: str


def parse_street_address(value: str) -> ParsedAddress:
    """Best-effort split of "street, city, ST 62704[-1234]" (US-only, like REQ-061).

    Parsing never rejects: unrecognized parts stay in ``street_line`` with the
    other components empty, so callers fall back to postal lookup or manual
    entry instead of failing a write over an address they can still store.
    """
    parts = [part.strip() for part in value.split(",")]
    if len(parts) >= 3:
        tail = _STATE_ZIP.match(parts[-1])
        if tail is not None:
            return ParsedAddress(
                street_line=", ".join(parts[:-2]),
                city_name=parts[-2],
                state_code=tail.group("state").upper(),
                postal_code=normalize_postal_code(tail.group("zip")),
            )
    return ParsedAddress(
        street_line=" ".join(value.split()), city_name="", state_code="", postal_code=""
    )


def normalize_postal_code(value: str) -> str:
    """Normalize a postal code to its lookup key: trimmed, upper, ZIP+4 → 5 digits."""
    text = value.strip().upper()
    zip_match = _ZIP_PLUS_FOUR.match(text)
    return zip_match.group(1) if zip_match else text


def postal_lookup(
    session: Session, postal_code: str, *, country_code: str = "US"
) -> PostalCode | None:
    """The postal → city/state reference read (REQ-061) behind form auto-fill.

    Looks up the live row for the normalized code; ``None`` means unknown, not
    invalid — the reference table is a convenience, never a validation gate.
    """
    return session.scalars(
        select(PostalCode)
        .where(PostalCode.deleted_at.is_(None))
        .where(PostalCode.country_code == country_code)
        .where(PostalCode.postal_code_value == normalize_postal_code(postal_code))
    ).first()


def normalized_shadow_values(
    registry: dict[str, SchemaRegistry], values: dict[str, Any]
) -> dict[str, str]:
    """Compute every ``<fieldName>Normalized`` shadow value a write must persist.

    Reads the same registry-declared ``duplicateMatchRules`` the duplicate
    detector matches against, so a field's shadow column and its match
    predicate can never disagree. ``None`` values produce no shadow entry —
    an absent value matches nothing.
    """
    shadow: dict[str, str] = {}
    for name, value in values.items():
        row = registry.get(name)
        if row is None or value is None:
            continue
        if (row.validation_rules or {}).get("duplicateMatchRules"):
            shadow[f"{name}Normalized"] = normalize_for_match(row.field_type, value)
    return shadow
