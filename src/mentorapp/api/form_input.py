"""Smart input parsing & formatting design (REQ-034, WTK-058).

The form-side companion to ``form_validation`` (REQ-033): where that module
decides whether a value is VALID, this one decides how a typed value is
PRESENTED and how pasted free text lands in components. Like the other
form designs, this is surface the shell invokes verbatim — no frontend
shell existed when it was written, so the contract lives here with the
engine, not in a screen.

The three capabilities, and where each runs:

- :func:`auto_format` — display auto-formatting for typed fields (phone,
  email, website, postal code). Runs on focus-exit, on the value
  ``form_validation.normalized_input`` produced and BEFORE
  ``validate_on_exit`` sees it, so what the user sees, what validates, and
  what the payload carries are the same string. Formatting is a convenience,
  NEVER a gate: input the formatter doesn't recognize comes back as typed
  (trimmed), and validity stays ``form_validation``'s job.
- :func:`resolve_paste` (+ :data:`PASTE_RESOLVABLE_TYPES`) — composite-field
  paste resolution. Confident components fill in, the unresolved remainder
  stays visible in the target control, and the paste is never blocked. The
  splitting itself is ``automation.normalization``'s
  ``parse_person_name`` / ``parse_street_address`` — the SAME parsers that
  feed the duplicate-match shadow columns, so a paste can never resolve
  differently than the server would match it (one canonical home, DB-S13).
- :func:`postal_autofill` — the postal → city/state convenience read over
  ``automation.normalization.postal_lookup`` (REQ-061 reference rows). The
  form runs it whenever a postal-code component lands — typed OR filled by
  an address paste — so there is exactly one auto-fill path. It fills EMPTY
  city/state controls only (never overwrites what the user typed), and
  ``None`` means unknown, not invalid: the reference table is a convenience,
  never a validation gate.

Phone normalization note (REQ-034): the display format is US ``(NNN)
NNN-NNNN``; equality/matching stays digits-only via
``normalize_for_match`` — formatting punctuation can never split a
duplicate match.

Endpoint wiring is deliberately out of scope here: ``postal_autofill``
takes a session and becomes a thin GET when the form screens land; the
formatters and paste resolvers are pure and can run client-side from the
same contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

from mentorapp.automation.normalization import (
    normalize_phone,
    normalize_postal_code,
    parse_person_name,
    parse_street_address,
    postal_lookup,
)


def format_phone(value: str) -> str:
    """Format a US phone for display: ``(555) 123-4567``, ``+1`` when given.

    Anything that isn't 10 digits (or 11 with a leading 1) comes back
    trimmed as typed — international and extension-bearing numbers are
    stored verbatim rather than mangled into a US shape.
    """
    digits = normalize_phone(value)
    if len(digits) == 11 and digits.startswith("1"):
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return value.strip()


def format_email(value: str) -> str:
    """Trim and lowercase an email address.

    Lowercasing the whole address (not just the domain) deliberately mirrors
    ``normalize_for_match``'s case-insensitive equality, so the displayed
    value and the duplicate-match value can never disagree on case.
    """
    return value.strip().lower()


def format_website(value: str) -> str:
    """Give a pasted/typed URL a scheme and a lowercase scheme + host.

    ``example.com/Path`` becomes ``https://example.com/Path`` — https is the
    only default worth having. Path, query, and fragment keep their case
    (many servers are case-sensitive there); only scheme and host lower.
    """
    text = value.strip()
    if not text:
        return text
    if "://" not in text:
        text = f"https://{text}"
    parts = urlsplit(text)
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, parts.fragment)
    )


def format_postal_code(value: str) -> str:
    """Format a postal code as its canonical lookup key (trimmed, upper, ZIP+4 → 5)."""
    return normalize_postal_code(value)


# fieldType → formatter, the whole auto-format vocabulary (REQ-034). Types not
# listed here are returned untouched — auto-format must never rewrite free text.
_FORMATTERS = {
    "phone": format_phone,
    "email": format_email,
    "website": format_website,
    "postalCode": format_postal_code,
}


def auto_format(field_type: str, value: str) -> str:
    """Auto-format one typed value for display and submission (REQ-034).

    Dispatches on the schema-registry ``fieldType``; unknown types pass
    through unchanged. Never raises and never rejects — an unformattable
    value is returned as typed and left for validation to judge.
    """
    formatter = _FORMATTERS.get(field_type)
    return formatter(value) if formatter is not None else value


@dataclass(frozen=True, slots=True)
class PasteResolution:
    """What one composite paste resolved to (REQ-034).

    ``components`` maps camelCase component field names to confident fills;
    ``remainder`` is the text that did not resolve and MUST stay visible in
    the pasted-into control — never discarded. Both may be non-empty at
    once; the paste itself is never blocked.
    """

    components: dict[str, str] = field(default_factory=dict)
    remainder: str = ""


def resolve_name_paste(text: str) -> PasteResolution:
    """Resolve a pasted full name into ``firstName`` / ``lastName``.

    Delegates the split to ``parse_person_name`` — the same rules the name
    match columns use (single token = last name, "Last, First" honored) —
    and fills only the parts the parser produced.
    """
    parsed = parse_person_name(text)
    components = {
        name: value
        for name, value in (("firstName", parsed.first_name), ("lastName", parsed.last_name))
        if value
    }
    return PasteResolution(components=components, remainder="" if components else text.strip())


def resolve_address_paste(text: str) -> PasteResolution:
    """Resolve a pasted single-line address into its components.

    ``parse_street_address`` is deliberately conservative: it either splits
    "street, city, ST zip" confidently or leaves everything in the street
    line. A confident split fills all four components; anything less keeps
    the whole paste as the visible remainder for hand-finishing — REQ-034's
    ambiguity rule, never a guess presented as fact.
    """
    parsed = parse_street_address(text)
    if parsed.city_name:
        return PasteResolution(
            components={
                "streetLine": parsed.street_line,
                "cityName": parsed.city_name,
                "stateCode": parsed.state_code,
                "postalCode": parsed.postal_code,
            }
        )
    return PasteResolution(remainder=parsed.street_line)


# Composite fieldTypes that accept a smart paste, and their resolvers. The
# shell consults this table — paste handling is declarative, never per-form.
PASTE_RESOLVERS = {
    "personName": resolve_name_paste,
    "address": resolve_address_paste,
}
PASTE_RESOLVABLE_TYPES = frozenset(PASTE_RESOLVERS)


def resolve_paste(field_type: str, text: str) -> PasteResolution:
    """Resolve pasted free text for one composite field (REQ-034).

    Unknown field types resolve nothing: the full text stays as remainder,
    which for a plain control simply means the paste lands as typed — the
    never-block rule holds for every field, resolvable or not.
    """
    resolver = PASTE_RESOLVERS.get(field_type)
    if resolver is None:
        return PasteResolution(remainder=text)
    return resolver(text)


def postal_autofill(
    session: Session, postal_code: str, *, country_code: str = "US"
) -> dict[str, str] | None:
    """Look up city/state for a postal code (REQ-034 auto-fill, REQ-061 rows).

    Returns ``{"cityName": ..., "stateCode": ...}`` for a known code, else
    ``None`` — unknown, not invalid. The form applies the fill to EMPTY
    city/state controls only; a user-typed value is never overwritten.
    """
    row = postal_lookup(session, postal_code, country_code=country_code)
    if row is None:
        return None
    return {"cityName": row.city_name, "stateCode": row.state_code}
