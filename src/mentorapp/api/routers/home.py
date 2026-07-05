"""``/home`` — the Home screen surface: frame, Areas, dashlets, admin messaging.

The build of the WTK-019 design (REQ-003, REQ-011): no frontend shell exists
yet (PI-002), so the shell renders exactly what these endpoints serve —
``mentorapp.ui.home_panel`` stays the one home for the behavior, this router
only speaks it over the envelope.

- ``GET /home`` composes the screen: the REQ-003 frame, the user's Areas
  rail, the dashlet list (messages dashlet always first, broken dashlets
  visible with an educate notice), and the message dashlet's content —
  which auto-reads on view, per the REQ-011 invariant.
- ``GET /home/banner`` is what every OTHER panel polls: urgent messages the
  user has not read, plus the unread badge count.
- ``POST /home/messages/{key}/read`` is the banner's open-the-message act;
  ``POST /home/messages/{key}/acknowledge`` is the only way acknowledgment
  ever happens (explicit click, never implicit).
- ``POST /home/messages`` publishes and ``GET /home/messages/{key}/
  acknowledgments`` is the admin's who-has-not-acknowledged audit.

Two provider seams follow the auth-router pattern (fail loudly until wired;
tests and deployments override the provider keys): :func:`get_home_catalog`
for the grant-derived Areas/views the composition needs (REQ-006 — the
WTK-025 derivation, bound when the panel catalog lands), and
:func:`get_message_center` for message persistence — the storage area backs
:class:`~mentorapp.ui.home_panel.MessageCenter` with a table and rebinds the
provider; an in-process default here would silently drop read and
acknowledgment state on every restart, which is worse than a clear error.

The user's dashlet arrangement rides the one preference mechanism (REQ-060)
under ``home.dashlets`` — read here with the same own-row-else-org-default
rule ``GET /preferences`` applies, never a second persistence path.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.envelope import Envelope, field_error, ok
from mentorapp.api.errors import ApiValidationError, RecordNotFoundError
from mentorapp.observability import get_logger
from mentorapp.storage import UserPreference, utcnow, uuid7
from mentorapp.ui.auth_flows import EducateMessage
from mentorapp.ui.home_panel import (
    HOME_DASHLETS_PREFERENCE_KEY,
    HOME_FRAME,
    AcknowledgmentNotRequestedError,
    AdminMessage,
    DashletRef,
    MessageCenter,
    MessagePriority,
    UnknownMessageError,
    resolve_home_dashlets,
)

log = get_logger(__name__)

router = APIRouter()

CODE_ACK_NOT_REQUESTED = "acknowledgmentNotRequested"

_MESSAGE_ENTITY = "adminMessage"


class HomeCatalog(Protocol):
    """What composing Home needs to know about one user's permissioned world.

    Both answers derive from the REQ-006 grant boundary (the WTK-025 rule:
    panel permission IS data-source permission) — this seam exists so the
    router never re-derives them.
    """

    def accessible_panel_keys(self, user_id: uuid.UUID) -> Sequence[str]:
        """Panel keys the user may open, in rail order (system default first)."""
        ...

    def available_view_keys(self, user_id: uuid.UUID) -> Collection[str]:
        """View keys the user may render as dashlets."""
        ...


def get_home_catalog() -> HomeCatalog:
    """Provide the grant-derived catalog; wiring binds it, tests override it."""
    raise RuntimeError(
        "home catalog provider is not wired; install home wiring or override get_home_catalog."
    )


def get_message_center() -> MessageCenter:
    """Provide message persistence; the storage-backed center rebinds this.

    Fail-loud, never an in-process default: a per-worker in-memory center
    would silently lose read/acknowledgment state on restart and disagree
    across workers.
    """
    raise RuntimeError(
        "message center provider is not wired; install home wiring or "
        "override get_message_center."
    )


_SessionDep = Annotated[Session, Depends(get_session)]
_UserDep = Annotated[uuid.UUID, Depends(get_current_user_id)]
_CatalogDep = Annotated[HomeCatalog, Depends(get_home_catalog)]
_CenterDep = Annotated[MessageCenter, Depends(get_message_center)]


def _educate_payload(notice: EducateMessage | None) -> dict[str, str] | None:
    if notice is None:
        return None
    return {
        "whatHappened": notice.what_happened,
        "why": notice.why,
        "whatNext": notice.what_next,
    }


def _frame_payload() -> dict[str, Any]:
    return {
        "logoZone": HOME_FRAME.logo_zone,
        "identityZone": HOME_FRAME.identity_zone,
        "areasZone": HOME_FRAME.areas_zone,
        "headerRight": list(HOME_FRAME.header_right),
        "accountMenu": [
            {"key": item.key, "label": item.label} for item in HOME_FRAME.account_menu
        ],
    }


def _message_payload(
    center: MessageCenter, message: AdminMessage, user_key: str
) -> dict[str, Any]:
    return {
        "messageKey": message.key,
        "title": message.title,
        "body": message.body,
        "postedBy": message.posted_by,
        "postedAt": message.posted_at,
        "expiresAt": message.expires_at,
        "priority": message.priority.value,
        "requiresAcknowledgment": message.requires_acknowledgment,
        "acknowledged": center.has_acknowledged(user_key, message.key),
    }


def _chosen_dashlets(session: Session, user_id: uuid.UUID) -> tuple[DashletRef, ...]:
    """The user's saved dashlet arrangement — the REQ-060 read rule.

    Own row overrides the org default, exactly as ``GET /preferences``
    resolves. No row (or no usable entries) means no chosen dashlets — Home
    then shows the messages dashlet alone, a normal first-login state.
    Entries missing ``viewKey``/``title`` are skipped with a log line: the
    document is machine-written client state, so a malformed entry is a
    client defect to surface in logs, not a render to guess at.
    """
    rows = session.scalars(
        select(UserPreference)
        .where(UserPreference.deleted_at.is_(None))
        .where(UserPreference.preference_key == HOME_DASHLETS_PREFERENCE_KEY)
        .where(or_(UserPreference.user_id == user_id, UserPreference.user_id.is_(None)))
    ).all()
    row = next((r for r in rows if r.user_id == user_id), None)
    row = row or next((r for r in rows if r.user_id is None), None)
    if row is None:
        return ()
    entries = row.preference_value.get("dashlets")
    if not isinstance(entries, list):
        return ()
    chosen: list[DashletRef] = []
    for entry in entries:
        view_key = entry.get("viewKey") if isinstance(entry, dict) else None
        title = entry.get("title") if isinstance(entry, dict) else None
        if isinstance(view_key, str) and isinstance(title, str):
            chosen.append(DashletRef(view_key, title))
        else:
            log.warning(
                "malformed home.dashlets entry skipped",
                extra={"context": {"userID": str(user_id), "entry": repr(entry)}},
            )
    return tuple(chosen)


@router.get("/home")
def get_home(
    session: _SessionDep,
    user_id: _UserDep,
    catalog: _CatalogDep,
    center: _CenterDep,
) -> Envelope:
    """Compose the Home screen: frame, Areas rail, dashlets, and messages.

    Rendering the message dashlet READS its messages (REQ-011 auto-read on
    view), so ``meta.unreadCount`` reports the badge value this open clears —
    a repeat open returns 0. Broken dashlets stay in ``data.dashlets`` with
    an educate ``notice``; the messages dashlet is always first. Fails 500
    when the catalog/message providers are unwired; 422 without ``X-User-ID``.
    """
    now = utcnow()
    user_key = str(user_id)
    unread_count = center.unread_count(user_key, now)
    messages = center.view_home(user_key, now)
    dashlets = resolve_home_dashlets(
        _chosen_dashlets(session, user_id), catalog.available_view_keys(user_id)
    )
    return ok(
        data={
            "frame": _frame_payload(),
            "areas": list(HOME_FRAME.areas_for(tuple(catalog.accessible_panel_keys(user_id)))),
            "dashlets": [
                {
                    "viewKey": d.view_key,
                    "title": d.title,
                    "notice": _educate_payload(d.notice),
                }
                for d in dashlets
            ],
            "messages": [_message_payload(center, m, user_key) for m in messages],
        },
        meta={"unreadCount": unread_count},
    )


@router.get("/home/banner")
def get_banner(user_id: _UserDep, center: _CenterDep) -> Envelope:
    """Urgent messages this user has NOT read — every panel banners these.

    Reading (not acknowledgment) clears a message from here, via ``GET
    /home`` or ``POST /home/messages/{key}/read``. ``meta.unreadCount`` is
    the header badge: ALL unexpired unread messages, not just urgent ones.
    """
    now = utcnow()
    user_key = str(user_id)
    return ok(
        data={
            "messages": [
                _message_payload(center, m, user_key)
                for m in center.urgent_banner(user_key, now)
            ]
        },
        meta={"unreadCount": center.unread_count(user_key, now)},
    )


class MessagePostBody(BaseModel):
    """POST body for publishing one admin message."""

    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    priority: MessagePriority = MessagePriority.NORMAL
    requires_acknowledgment: bool = Field(default=False, alias="requiresAcknowledgment")
    expires_at: AwareDatetime | None = Field(default=None, alias="expiresAt")


@router.post("/home/messages")
def post_message(body: MessagePostBody, user_id: _UserDep, center: _CenterDep) -> Envelope:
    """Publish an admin message to every user (REQ-011).

    ``postedBy`` is stamped from the acting identity, never taken from the
    body — the dashlet's posted-by line must be auditable. Posting rights
    ride the admin surface's data-source grant once that catalog is wired
    (REQ-006); today the trusted front end is the caller, as everywhere
    behind ``X-User-ID``.
    """
    message = AdminMessage(
        key=str(uuid7()),
        title=body.title,
        body=body.body,
        posted_by=str(user_id),
        posted_at=utcnow(),
        expires_at=body.expires_at,
        priority=body.priority,
        requires_acknowledgment=body.requires_acknowledgment,
    )
    center.post(message)
    return ok(data=_message_payload(center, message, str(user_id)))


@router.post("/home/messages/{message_key}/read")
def read_message(message_key: str, user_id: _UserDep, center: _CenterDep) -> Envelope:
    """The banner's open-the-message act: render ONE message, which reads it.

    Scoped on purpose — opening a banner from another panel must not read
    the Home messages the user never saw. 404 for unknown AND expired keys
    alike (an expired message has left every surface).
    """
    try:
        message = center.view_message(str(user_id), message_key, utcnow())
    except UnknownMessageError:
        raise RecordNotFoundError(_MESSAGE_ENTITY, message_key) from None
    return ok(data=_message_payload(center, message, str(user_id)))


@router.post("/home/messages/{message_key}/acknowledge")
def acknowledge_message(message_key: str, user_id: _UserDep, center: _CenterDep) -> Envelope:
    """Record this user's EXPLICIT acknowledgment click (REQ-011).

    The only path to acknowledged state — no view or banner ever implies it.
    Acknowledging also reads (the click sits on the rendered message), so an
    urgent banner never outlives an acknowledgment. 422
    ``acknowledgmentNotRequested`` when the message never asked — that is a
    caller bug, not a user state.
    """
    user_key = str(user_id)
    try:
        center.acknowledge(user_key, message_key)
    except UnknownMessageError:
        raise RecordNotFoundError(_MESSAGE_ENTITY, message_key) from None
    except AcknowledgmentNotRequestedError:
        raise ApiValidationError(
            [
                field_error(
                    "messageKey",
                    CODE_ACK_NOT_REQUESTED,
                    "This message does not ask for acknowledgment; reading it "
                    "is all that is needed.",
                )
            ]
        ) from None
    # No message body here: acknowledging must work on an EXPIRED message
    # (a banner opened just before expiry; the books never close), and the
    # render paths rightly refuse expired keys. The caller already holds the
    # rendered message it clicked on.
    return ok(data={"messageKey": message_key, "acknowledged": True})


@router.get("/home/messages/{message_key}/acknowledgments")
def message_acknowledgments(
    message_key: str,
    center: _CenterDep,
    user_id: _UserDep,
    roster: Annotated[list[str] | None, Query(alias="userId")] = None,
) -> Envelope:
    """The admin audit: who has, and has not, acknowledged one message.

    ``userId`` repeats once per roster member — the roster lives in the
    admin's user data source, not here, so the caller supplies who should
    have acknowledged. Expiration never closes the books: an expired
    message answers this audit like a live one.
    """
    roster_keys = tuple(roster or ())
    try:
        outstanding = center.outstanding_acknowledgments(message_key, roster_keys)
    except UnknownMessageError:
        raise RecordNotFoundError(_MESSAGE_ENTITY, message_key) from None
    except AcknowledgmentNotRequestedError:
        raise ApiValidationError(
            [
                field_error(
                    "messageKey",
                    CODE_ACK_NOT_REQUESTED,
                    "This message never asked for acknowledgment, so there is "
                    "no acknowledgment state to audit.",
                )
            ]
        ) from None
    return ok(
        data={
            "messageKey": message_key,
            "acknowledged": [u for u in roster_keys if u not in outstanding],
            "outstanding": list(outstanding),
        }
    )
