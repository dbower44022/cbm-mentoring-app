"""Test-only identity binding: the test names the acting user per request.

Production resolves the acting user SERVER-SIDE from the opaque session
reference (``mentorapp.api.deps.get_current_user_id`` — FND-909 D9); the API
unit suites are not session-lifecycle tests, so they bind this stub over that
dependency and keep declaring the acting user directly via the ``X-User-ID``
header their requests already carry. The real resolution path — unknown /
stale / expired references answering the structured 401s — is covered by
``tests/test_api_session_identity.py`` and exercised end-to-end by the
Playwright journeys over ``tests/e2e_harness.py``.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Header


def header_user_id(
    x_user_id: Annotated[uuid.UUID, Header(alias="X-User-ID")],
) -> uuid.UUID:
    """The stubbed identity seam: trust the test's ``X-User-ID`` header."""
    return x_user_id
