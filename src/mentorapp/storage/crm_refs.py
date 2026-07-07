"""CRM reference entities — the REQ-062/REQ-063 ownership boundary (WTK-150, WTK-164).

The organization's CRM remains the system of record for organization and
mentor master data (REQ-062). These tables are the application's *references*
to that truth, never a fork of it: each row is an identity anchor — an
entity-named UUIDv7 key on the app side plus the CRM record's own id — and
deliberately carries **no master-data columns**. Names and other master
fields are read from the CRM (``mentorapp.crm``) and written back to it;
storing them here would create the second source of truth REQ-062 forbids.

PI-010 reconciliation (REQ-086, one canonical home per concept — SKL-122):
the former ``crmClientRef`` anchored "a client record mastered in the CRM",
but REQ-086 fixes the model as ONE organization record with *client* and
*partner* as role subclasses of the company. The record the CRM masters is
the organization, so the anchor is now :class:`CrmCompanyRef` (renamed by
migration 0014, never duplicated), and the role subclasses — app-owned
working data with no CRM home — live in :mod:`mentorapp.storage.mentoring`
(``client``/``partner``), each 1:1 on this anchor. The former
``crmEngagementRef`` was likewise reconciled INTO the app-owned
``engagement`` entity (see :mod:`mentorapp.storage.mentoring` for why the
engagement's working home moved app-side).

The CRM record ids are entity-named (``crmCompanyID``, ``crmMentorID``)
rather than a shared bare ``crmID`` — DB-R2's system-wide name uniqueness is
mechanically enforced by the registry seed, and each id means one specific
thing: "this record's id in the CRM". One live anchor per CRM record
(partial unique per DB-S3); a soft-deleted anchor never blocks re-anchoring
the same CRM record.
"""

from __future__ import annotations

import uuid

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from mentorapp.storage.entity import BaseEntity, entity_key, entity_ref, live_unique

# EspoCRM record ids are 17-character strings today; the width is not a
# contract we own, so leave headroom rather than encode the quirk.
_CRM_ID_LENGTH = 64

# Re-pointing an anchor at a different CRM record silently changes what every
# linked app-owned row is about — history-track the id so the swap is
# auditable (DB-S5 per-field flag).
_TRACKED = {"historyTrackedFlag": True}


class CrmCompanyRef(BaseEntity):
    """App-side anchor for THE organization record mastered in the CRM (REQ-086).

    One row per organization the app works with — never one per role. The
    client and partner roles a company plays are app-owned subclass rows
    (:class:`~mentorapp.storage.mentoring.Client` /
    :class:`~mentorapp.storage.mentoring.Partner`), each keyed 1:1 on this
    anchor, which is exactly REQ-086's "no duplicate company rows per role".
    """

    __tablename__ = "crmCompanyRef"
    __ownership_side__ = "crm"
    __table_args__ = (live_unique("uq_crmCompanyRef_crmCompanyID_live", "crmCompanyID"),)

    crm_company_ref_id: Mapped[uuid.UUID] = entity_key("crmCompanyRefID")
    crm_company_id: Mapped[str] = mapped_column(
        "crmCompanyID",
        String(_CRM_ID_LENGTH),
        nullable=False,
        info={"registry": {"fieldLabel": "CRM Company ID", **_TRACKED}},
    )


class CrmMentorRef(BaseEntity):
    """App-side anchor for a mentor record mastered in the CRM (REQ-062).

    ``userID`` is the REQ-019 pairing the engagement scoping stands on
    (WTK-167): which ``appUser`` this mentor IS in the app. Mentor identity
    lives in the CRM (the WTK-003 identity bridge provisions ``appUser`` by
    ``crmUserID`` at login), so the pairing is a linkage, not master data.
    Nullable because a mentor's CRM record can be anchored before that person
    ever signs in; at most one live anchor per app user (partial unique), so
    "this user's engagements" can never mean two mentors.
    """

    __tablename__ = "crmMentorRef"
    __ownership_side__ = "crm"
    __table_args__ = (
        live_unique("uq_crmMentorRef_crmMentorID_live", "crmMentorID"),
        live_unique("uq_crmMentorRef_userID_live", "userID"),
    )

    crm_mentor_ref_id: Mapped[uuid.UUID] = entity_key("crmMentorRefID")
    crm_mentor_id: Mapped[str] = mapped_column(
        "crmMentorID",
        String(_CRM_ID_LENGTH),
        nullable=False,
        info={"registry": {"fieldLabel": "CRM Mentor ID", **_TRACKED}},
    )
    # Re-pairing a mentor anchor to a different app user re-scopes every
    # engagement read that filters on it — history-track the swap (DB-S5).
    user_id: Mapped[uuid.UUID | None] = entity_ref(
        "appUser.userID",
        nullable=True,
        registry={"fieldLabel": "User ID", **_TRACKED},
    )
