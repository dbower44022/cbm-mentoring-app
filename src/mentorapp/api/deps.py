"""API dependencies — the request-scoped database session and the acting user.

The engine comes from ``MENTORAPP_DATABASE_URL`` and is created once, lazily.
An unset URL fails loudly at first use: a silently-defaulted database is a
worse failure mode than a clear startup error. Tests override ``get_session``
via ``app.dependency_overrides`` and never touch the environment.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from mentorapp.access import SessionManagement, SessionNotFoundError


@lru_cache(maxsize=1)
def _engine() -> Engine:
    url = os.environ.get("MENTORAPP_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "MENTORAPP_DATABASE_URL is not set; the API cannot open a database session."
        )
    return create_engine(url)


def get_session() -> Iterator[Session]:
    """Yield one session per request; closed when the response is sent."""
    with Session(_engine()) as session:
        yield session


def get_session_management() -> SessionManagement:
    """Provide the session process; deployments and tests override this.

    Fail-loud like :func:`_engine`: an unwired auth backend must be a clear
    server error, never a silently permissive fallback. Lives here (not in
    the auth router) because the identity seam below consumes it on EVERY
    authenticated request, not only on the ``/auth`` surface.
    """
    raise RuntimeError("session management is not wired; override get_session_management")


def get_current_user_id(
    sessions: Annotated[SessionManagement, Depends(get_session_management)],
    session_reference: Annotated[str | None, Header(alias="X-Session-Reference")] = None,
) -> uuid.UUID:
    """The acting session user for this request — the ONE identity seam.

    SECURITY (FND-909 D9, session lifecycle standard SKL-113): the acting
    user is resolved SERVER-SIDE from the opaque session reference, on every
    authenticated read and write. The previous contract trusted a
    client-supplied ``X-User-ID`` header — client-claimed identity — which
    let any caller act as any user, and let a stale browser session (a user
    ID from a database that had since been rebuilt) reach user-scoped writes
    and crash on a foreign-key violation instead of being refused. The header
    is gone entirely rather than kept as a cross-check: two identity inputs
    can only ever drift, and the session record already carries the truth.

    The :class:`SessionManagement` exceptions propagate deliberately — the
    registered error handlers map them to the client contract: an unknown or
    ended reference (including one from a rebuilt database) answers the
    structured ``unauthenticated`` 401 (the client lands signed out), and an
    expired-but-revivable session answers ``reauthRequired`` 401 (the client
    re-authenticates in place). Never a 500.
    """
    if session_reference is None:
        # Same refusal as an unknown reference: absence of a session and a
        # dead session are one client outcome — sign in.
        raise SessionNotFoundError("no session reference presented")
    return sessions.resolve(session_reference).user_id
