"""CRM reference entities — the REQ-062/REQ-063 ownership boundary (WTK-150).

The organization's CRM remains the system of record for mentor, client, and
engagement master data (REQ-062). These three tables are the application's
*references* to that truth, never a fork of it: each row is an identity
anchor — an entity-named UUIDv7 key on the app side plus the CRM record's
own id — and deliberately carries **no master-data columns**. Names,
statuses, focus areas, and the mentor↔client↔engagement associations are
read from the CRM (``mentorapp.crm``) and written back to it; storing them
here would create the second source of truth REQ-062 forbids.

The anchors exist for the app-owned side of the boundary (REQ-063):
mentoring-process rows this store owns — sessions, notes, next steps —
foreign-key the UUIDv7 anchor (``crmEngagementRefID`` et al.) under the
normal entity-naming rule, while the anchor's ``crm*ID`` column ties that
app-owned history to the CRM record it concerns. ``customAttributes`` on an
anchor is likewise app-owned: admin-defined attributes attach to a
CRM-mastered record app-side without ever writing into the CRM's schema.

The CRM record ids are entity-named (``crmClientID``, ``crmEngagementID``,
``crmMentorID``) rather than a shared bare ``crmID`` — DB-R2's system-wide
name uniqueness is mechanically enforced by the registry seed, and each id
means one specific thing: "this record's id in the CRM". One live anchor
per CRM record (partial unique per DB-S3); a soft-deleted anchor never
blocks re-anchoring the same CRM record.
"""

from __future__ import annotations

import uuid

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from mentorapp.storage.entity import BaseEntity, entity_key, live_unique

# EspoCRM record ids are 17-character strings today; the width is not a
# contract we own, so leave headroom rather than encode the quirk.
_CRM_ID_LENGTH = 64

# Re-pointing an anchor at a different CRM record silently changes what every
# linked app-owned row is about — history-track the id so the swap is
# auditable (DB-S5 per-field flag).
_TRACKED = {"historyTrackedFlag": True}


class CrmClientRef(BaseEntity):
    """App-side anchor for a client record mastered in the CRM (REQ-062)."""

    __tablename__ = "crmClientRef"
    __table_args__ = (live_unique("uq_crmClientRef_crmClientID_live", "crmClientID"),)

    crm_client_ref_id: Mapped[uuid.UUID] = entity_key("crmClientRefID")
    crm_client_id: Mapped[str] = mapped_column(
        "crmClientID",
        String(_CRM_ID_LENGTH),
        nullable=False,
        info={"registry": {"fieldLabel": "CRM Client ID", **_TRACKED}},
    )


class CrmEngagementRef(BaseEntity):
    """App-side anchor for an engagement record mastered in the CRM (REQ-062)."""

    __tablename__ = "crmEngagementRef"
    __table_args__ = (
        live_unique("uq_crmEngagementRef_crmEngagementID_live", "crmEngagementID"),
    )

    crm_engagement_ref_id: Mapped[uuid.UUID] = entity_key("crmEngagementRefID")
    crm_engagement_id: Mapped[str] = mapped_column(
        "crmEngagementID",
        String(_CRM_ID_LENGTH),
        nullable=False,
        info={"registry": {"fieldLabel": "CRM Engagement ID", **_TRACKED}},
    )


class CrmMentorRef(BaseEntity):
    """App-side anchor for a mentor record mastered in the CRM (REQ-062)."""

    __tablename__ = "crmMentorRef"
    __table_args__ = (live_unique("uq_crmMentorRef_crmMentorID_live", "crmMentorID"),)

    crm_mentor_ref_id: Mapped[uuid.UUID] = entity_key("crmMentorRefID")
    crm_mentor_id: Mapped[str] = mapped_column(
        "crmMentorID",
        String(_CRM_ID_LENGTH),
        nullable=False,
        info={"registry": {"fieldLabel": "CRM Mentor ID", **_TRACKED}},
    )
