"""The production HTTP transport behind the EspoCRM binding (WTK-010).

:class:`HttpxEspoTransport` is the real :class:`~mentorapp.crm.espo.EspoTransport`
plug: one ``httpx.Client`` against an Espo site's ``api/v1`` base. It lives
apart from :mod:`mentorapp.crm.espo` so the binding's request/outcome policy
stays free of HTTP machinery and tests keep plugging in-memory fakes.

Per the transport contract, ``send`` returns an
:class:`~mentorapp.crm.espo.EspoResponse` for ANY HTTP status — status
handling is gateway policy — and raises only when no response could be
obtained at all; the gateway maps that raise to
:class:`~mentorapp.crm.auth.CrmUnavailableError`, so nothing is swallowed here.

:func:`espo_gateway_from_env` is the deployment plug point
(``MENTORAPP_ESPO_URL``), fail-loud like ``api/deps._engine``: a CRM binding
silently pointed nowhere is a worse failure mode than a clear startup error.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import httpx

from mentorapp.crm.espo import EspoAuthGateway, EspoResponse

_API_BASE_PATH = "api/v1"
_DEFAULT_TIMEOUT_SECONDS = 10.0


class HttpxEspoTransport:
    """One HTTP client speaking to one Espo site's ``api/v1`` API.

    ``site_url`` is the Espo site root (``https://crm.example.org``); the
    ``api/v1`` base is appended here so callers configure the address a human
    would recognise. ``transport`` is httpx's own seam, exposed for tests
    (``httpx.MockTransport``) — request policy stays in this class either way.
    """

    def __init__(
        self,
        site_url: str,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # The trailing slash matters: httpx joins relative request paths onto
        # the base URL, and without it the final path segment is replaced
        # instead of extended.
        self._client = httpx.Client(
            base_url=f"{site_url.rstrip('/')}/{_API_BASE_PATH}/",
            timeout=timeout_seconds,
            transport=transport,
        )

    def send(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> EspoResponse:
        """Perform one Espo API request; answer for any status, raise on none.

        Returns the decoded :class:`EspoResponse`; network-level failures
        propagate as httpx's exceptions for the gateway to contain. A body
        that is not JSON decodes to a ``None`` payload — Espo answers some
        statuses with empty or HTML bodies, and payload usability is judged
        by the gateway, not here.
        """
        response = self._client.request(
            method,
            path.lstrip("/"),
            headers=dict(headers),
            params=dict(params) if params is not None else None,
            json=dict(json) if json is not None else None,
        )
        try:
            payload: Any = response.json()
        except ValueError:
            payload = None
        return EspoResponse(status_code=response.status_code, payload=payload)

    def close(self) -> None:
        """Release the client's connection pool (process shutdown)."""
        self._client.close()


def espo_gateway_from_env() -> EspoAuthGateway:
    """Build the deployment :class:`EspoAuthGateway` from ``MENTORAPP_ESPO_URL``.

    Raises ``RuntimeError`` when the URL is not set — the CRM binding must
    never come up silently unconfigured.
    """
    site_url = os.environ.get("MENTORAPP_ESPO_URL")
    if not site_url:
        raise RuntimeError(
            "MENTORAPP_ESPO_URL is not set; the EspoCRM binding cannot be constructed."
        )
    return EspoAuthGateway(HttpxEspoTransport(site_url))
