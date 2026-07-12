"""The attendee contact-detail seam (REQ-110, PI-015).

The session details surface lists each attendee's email, phone, and company,
but contact master data lives in the CRM (REQ-062) — the app stores only the
engagement's primary-contact name/email and CRM anchors. This seam is the one
canonical home for "detail about a CRM contact the read surface wants to
show": the production binding reads the CRM; until that binding lands with
the outage read-path work, the dev default below answers deterministically
(the sanctioned dev-seam pattern the transcript and drafter seams set).

``lookup`` is batch-shaped on purpose: one call per rendered surface, never
one per attendee, so the production CRM binding can be a single filtered
read. A missing id in the answer means "no detail available" — the surface
renders the app-side values it already has and an em-dash for the rest,
never an error (the CRM being short on detail is not a defect of the
session).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ContactDetail:
    """What the read surface may show about one CRM-mastered person.

    Every field is optional: app-side values (the engagement's contact name,
    the company anchor) stay authoritative where they exist, and a source
    only fills gaps — a ``None`` here never blanks a value the app already
    knows.
    """

    contact_name: str | None = None
    email_address: str | None = None
    phone_number: str | None = None
    company_name: str | None = None


class ContactDetailSource:
    """Protocol-shaped seam: detail for a batch of CRM contact/mentor ids."""

    def lookup(self, crm_ids: Sequence[str]) -> Mapping[str, ContactDetail]:
        """Detail per CRM id; ids without detail are simply absent."""
        raise NotImplementedError


@dataclass(frozen=True)
class DeterministicContactDetailSource(ContactDetailSource):
    """Dev default: stable synthetic phone digits, nothing else.

    Deterministic from the id (same id → same digits) so rendered
    verification can pin values, and deliberately name/company-silent so the
    app-side values remain what the surface shows — the dev seam must never
    invent a person. Never a network call.
    """

    def lookup(self, crm_ids: Sequence[str]) -> Mapping[str, ContactDetail]:
        answer: dict[str, ContactDetail] = {}
        for crm_id in crm_ids:
            digest = hashlib.sha256(crm_id.encode()).digest()
            local = 100 + digest[0] % 900
            line = 1000 + int.from_bytes(digest[1:3], "big") % 9000
            answer[crm_id] = ContactDetail(phone_number=f"(216) 555-{local:03d}{line % 10}")
        return answer
