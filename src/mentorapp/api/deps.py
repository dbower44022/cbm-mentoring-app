"""API dependencies — the request-scoped database session.

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

from fastapi import Header
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session


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


def get_current_user_id(
    x_user_id: Annotated[uuid.UUID, Header(alias="X-User-ID")],
) -> uuid.UUID:
    """The acting session user for this request — the ONE identity seam.

    User-scoped reads and audit stamping depend on this function, never on the
    transport: staff identities live in the CRM system of record, and today the
    trusted front end supplies the user as the ``X-User-ID`` header. When
    bearer authentication lands, it rebinds this dependency — no endpoint
    changes. Missing or malformed → the standard per-field 422 envelope.
    """
    return x_user_id
