"""ViewAndSourcePermissions: view lifecycle rules + persona capabilities (WTK-044).

The access rules REQ-017/REQ-019/REQ-028 share, decided in one place:

- **View lifecycle (REQ-017).** A system view (``owner_id`` ``None``) is
  read-only for everyone: editing one can only end in save-as-user or
  apply-temporarily, never an in-place save. Users manage — update, rename,
  delete — only their OWN saved views; another user's view is invisible by
  design (user views are not sharable user-to-user), so foreign-view rules
  exist only for facts that arrive by reference (deep links). Promotion of a
  user view to a system view is the one sharing path, and it is an admin
  capability.
- **Data-source authoring (REQ-019).** Only a holder of the
  ``adminSql.author`` capability authors the sources everyone consumes.
  RUNNING a source stays the REQ-006 grant boundary
  (:mod:`~mentorapp.access.grants`) — authoring and running are different
  permissions with different homes, on purpose.
- **Deep-link gating (REQ-028).** Links are references, never grants. The
  data-source permission a link still requires IS
  :func:`~mentorapp.access.grants.roles_cover_data_source` — no second
  boundary — and the pure resolution rule (own/system view opens, foreign
  private view falls back to last-used with the notice) is
  ``mentorapp.api.grid_surface.resolve_grid_link``, which consumes this
  module's :func:`view_visible_to` fact. A denial renders as the standard
  no-access state, :data:`PERMISSION_REFUSAL`.

Capabilities are the decision unit; personas are the human-facing map over
them. The baseline set every signed-in user holds is intrinsic — never
persisted — while admin capabilities are per-user ``accessGrant`` rows
(WTK-001 storage), read here through :class:`StoredCapabilityRegistry`. A
partial holder (say, only ``gridView.promote``) is still described as the
user persona, but the capability they hold works: decisions check keys,
never the persona label.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.observability import get_logger
from mentorapp.storage import AccessGrant

log = get_logger(__name__)


# --- Capability vocabulary (app-validated data, never a DB enum — DB-S7) ---------

# Persisted admin capabilities: ``accessGrant.accessGrantKey`` values.
CAP_DATA_SOURCE_AUTHOR: Final = "adminSql.author"
CAP_VIEW_PROMOTE: Final = "gridView.promote"
# REQ-041: REGISTERING a workprocess is the Administrator persona's act —
# registration only; who SEES and LAUNCHES one stays the REQ-006 data-source
# boundary, deliberately not a capability (permission is inherited, never
# per-app).
CAP_WORKPROCESS_REGISTER: Final = "workprocess.register"

ADMIN_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {CAP_DATA_SOURCE_AUTHOR, CAP_VIEW_PROMOTE, CAP_WORKPROCESS_REGISTER}
)

# Intrinsic baseline: what being a signed-in user MEANS (REQ-017/REQ-028).
# Named so surfaces and tests can speak about them, but never stored — a
# baseline row could be revoked, and these must not be revocable.
CAP_VIEW_SAVE_AS_USER: Final = "gridView.saveAsUser"
CAP_VIEW_APPLY_TEMPORARY: Final = "gridView.applyTemporary"
CAP_VIEW_MANAGE_OWN: Final = "gridView.manageOwn"
CAP_LINK_SHARE: Final = "gridView.shareLink"

USER_BASELINE_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        CAP_VIEW_SAVE_AS_USER,
        CAP_VIEW_APPLY_TEMPORARY,
        CAP_VIEW_MANAGE_OWN,
        CAP_LINK_SHARE,
    }
)

PERSONA_USER: Final = "user"
PERSONA_ADMIN: Final = "admin"

# The persona → capability map the work task asks for, as data: what each
# persona may do across REQ-017 (view lifecycle), REQ-019 (source authoring),
# and REQ-028 (link sharing).
PERSONA_CAPABILITIES: Final[dict[str, frozenset[str]]] = {
    PERSONA_USER: USER_BASELINE_CAPABILITIES,
    PERSONA_ADMIN: USER_BASELINE_CAPABILITIES | ADMIN_CAPABILITIES,
}

# REQ-028's "standard no-access state": what a surface renders when a deep
# link (or any open attempt) is refused for lack of data-source permission.
PERMISSION_REFUSAL: Final = "permission_refusal"


def effective_capabilities(held_grant_keys: frozenset[str]) -> frozenset[str]:
    """Baseline plus the user's persisted admin grants.

    Intersecting with :data:`ADMIN_CAPABILITIES` means a stray or retired
    ``accessGrantKey`` in storage grants nothing here — the vocabulary is
    app-validated, so this module, not the row, decides what a key can mean.
    """
    return USER_BASELINE_CAPABILITIES | (held_grant_keys & ADMIN_CAPABILITIES)


def persona_for(held_grant_keys: frozenset[str]) -> str:
    """The descriptive label: admin only when the FULL admin set is held.

    Decisions never consult this — a partial holder's capability still works
    through :func:`authorize_capability`; the label is for surfaces that
    present "who am I" (shell chrome, admin entry points).
    """
    if held_grant_keys >= ADMIN_CAPABILITIES:
        return PERSONA_ADMIN
    return PERSONA_USER


class CapabilityError(Exception):
    """The user lacks the named capability; maps to a 403 envelope."""

    def __init__(self, capability: str, user_id: uuid.UUID) -> None:
        self.capability = capability
        self.user_id = user_id
        super().__init__(f"capability {capability!r} not held")


class CapabilityLookup(Protocol):
    """The persistence seam for per-user capability grants (WTK-001 storage)."""

    def grant_keys_held(self, user_id: uuid.UUID) -> frozenset[str]:
        """The live ``accessGrantKey`` values held by the user; empty when none."""
        ...


class InMemoryCapabilityRegistry:
    """Reference :class:`CapabilityLookup` for tests and pre-persistence wiring."""

    def __init__(self, grants: dict[uuid.UUID, frozenset[str]] | None = None) -> None:
        self._grants: dict[uuid.UUID, frozenset[str]] = dict(grants or {})

    def add(self, user_id: uuid.UUID, capability: str) -> None:
        self._grants[user_id] = self._grants.get(user_id, frozenset()) | {capability}

    def grant_keys_held(self, user_id: uuid.UUID) -> frozenset[str]:
        return self._grants.get(user_id, frozenset())


class StoredCapabilityRegistry:
    """:class:`CapabilityLookup` over the persisted ``accessGrant`` rows.

    Live rows only: revoking a capability (soft delete) makes the very next
    check stop honoring it, mirroring how
    :class:`~mentorapp.access.grants.StoredGrantRegistry` treats data-source
    grants — revocation needs no sweep because every decision re-reads.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def grant_keys_held(self, user_id: uuid.UUID) -> frozenset[str]:
        rows = self._session.scalars(
            select(AccessGrant.access_grant_key).where(
                AccessGrant.user_id == user_id,
                AccessGrant.deleted_at.is_(None),
            )
        )
        return frozenset(rows)


def holds_capability(lookup: CapabilityLookup, *, user_id: uuid.UUID, capability: str) -> bool:
    """The quiet form, for deriving what to SHOW (admin menus, authoring entry)."""
    return capability in effective_capabilities(lookup.grant_keys_held(user_id))


def authorize_capability(
    lookup: CapabilityLookup, *, user_id: uuid.UUID, capability: str
) -> None:
    """The attempt form: raise :class:`CapabilityError` unless the capability is held.

    Refusals are logged — an attempt at an admin action by a non-holder is an
    audit-relevant signal, same policy as data-source denials (REQ-006).
    """
    if holds_capability(lookup, user_id=user_id, capability=capability):
        return
    log.info(
        "capability refused",
        extra={"context": {"capability": capability, "userID": str(user_id)}},
    )
    raise CapabilityError(capability, user_id)


def authorize_data_source_authoring(lookup: CapabilityLookup, *, user_id: uuid.UUID) -> None:
    """REQ-019's gate, by name: only ``adminSql.author`` holders author sources.

    Authoring only — running a source remains
    :func:`~mentorapp.access.grants.authorize_data_source`.
    """
    authorize_capability(lookup, user_id=user_id, capability=CAP_DATA_SOURCE_AUTHOR)


# --- View lifecycle rules (REQ-017) ----------------------------------------------


@dataclass(frozen=True)
class ViewFacts:
    """A grid view as the permission decision sees it (storage: ``gridView``).

    ``owner_id`` ``None`` is a system view; ``read_only`` is the admin lock a
    promoted view may carry; ``temporary_modified`` marks the session-scoped
    working copy REQ-017 keeps until another view is chosen.
    """

    view_id: uuid.UUID
    owner_id: uuid.UUID | None
    read_only: bool = False
    temporary_modified: bool = False


# Where an edit may land — the editor offers exactly these (REQ-017): an
# in-place save when the view is the user's to change, otherwise save-as-user
# (apply-temporarily is always also available on anything visible).
SAVE_IN_PLACE: Final = "saveInPlace"
SAVE_AS_USER: Final = "saveAsUser"


def view_visible_to(view: ViewFacts, user_id: uuid.UUID) -> bool:
    """System views are for everyone (with data-source access); user views
    only for their owner. This is the fact behind REQ-028's private-view
    fallback: a foreign private view is invisible, not forbidden-with-detail."""
    return view.owner_id is None or view.owner_id == user_id


def save_disposition(view: ViewFacts, *, user_id: uuid.UUID) -> str:
    """Where saving THIS view's modifications may land for THIS user.

    In-place only for the owner's own saved, unlocked view. A system view, a
    locked view, a foreign view, and the session's temporary-modified working
    copy all route to save-as-user — the modified original is never touched
    (REQ-017: "a modified system view can only be saved as a user view").
    """
    if view.owner_id == user_id and not view.read_only and not view.temporary_modified:
        return SAVE_IN_PLACE
    return SAVE_AS_USER


def can_manage_view(view: ViewFacts, *, user_id: uuid.UUID) -> bool:
    """Update/rename/delete: the owner's own saved views only.

    System views are read-only for everyone; a locked view is the admin's to
    unlock first; the temporary-modified copy is session state, not a saved
    view — it is replaced by choosing another view, not managed.
    """
    return view.owner_id == user_id and not view.read_only and not view.temporary_modified


def can_apply_temporarily(view: ViewFacts, *, user_id: uuid.UUID) -> bool:
    """Apply-temporarily is open on anything the user can see (REQ-017):
    it creates their own session-scoped copy and touches nothing shared."""
    return view_visible_to(view, user_id)


class ViewPermissionError(Exception):
    """The view mutation is not this user's to make; maps to a 403 envelope."""

    def __init__(self, action: str, view_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.action = action
        self.view_id = view_id
        self.user_id = user_id
        super().__init__(f"view action {action!r} refused")


def authorize_view_management(view: ViewFacts, *, user_id: uuid.UUID, action: str) -> None:
    """The attempt form of :func:`can_manage_view`, logged like every refusal."""
    if can_manage_view(view, user_id=user_id):
        return
    log.info(
        "view management refused",
        extra={
            "context": {
                "action": action,
                "viewID": str(view.view_id),
                "userID": str(user_id),
                "systemView": view.owner_id is None,
            }
        },
    )
    raise ViewPermissionError(action, view.view_id, user_id)


def authorize_view_promotion(
    view: ViewFacts, *, lookup: CapabilityLookup, user_id: uuid.UUID
) -> None:
    """Promoting a user view to a system view — the one sharing path (REQ-017).

    Two gates, both required: the actor holds ``gridView.promote``, and the
    subject is a SAVED user view — a system view is already promoted, and a
    temporary-modified copy is session state with nothing durable to promote
    (its owner saves it as a user view first).
    """
    authorize_capability(lookup, user_id=user_id, capability=CAP_VIEW_PROMOTE)
    if view.owner_id is None or view.temporary_modified:
        log.info(
            "view promotion refused",
            extra={
                "context": {
                    "viewID": str(view.view_id),
                    "userID": str(user_id),
                    "systemView": view.owner_id is None,
                    "temporaryModified": view.temporary_modified,
                }
            },
        )
        raise ViewPermissionError("promote", view.view_id, user_id)
