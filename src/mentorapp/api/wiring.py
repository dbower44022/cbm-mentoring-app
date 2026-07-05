"""Production auth wiring: DB-backed stores + the Espo verifier behind the router seams.

:func:`install_auth_wiring` binds the four fail-loud providers of
``mentorapp.api.routers.auth`` (WTK-191) so the endpoints run against real
persistence (``StoredSessionStore``/``StoredTokenActionStore`` over the
request-scoped DB session) and EspoCRM via
:class:`~mentorapp.crm.espo.EspoAuthGateway`. Tests re-override the same
provider keys, so installing this wiring in the app factory changes nothing
for them.

Configuration is environment-read at request time and fails loudly when
absent (the ``deps._engine`` pattern — an unwired backend must be a clear
server error, never a silently permissive fallback):

- ``MENTORAPP_CREDENTIAL_KEY`` — urlsafe-base64, exactly 32 bytes decoded;
  the FND-006 server key sealing ``crmCredentialEncrypted``.
- ``MENTORAPP_TOKEN_SIGNING_KEY`` — urlsafe-base64; the action-link HMAC key
  (same key-management family, deliberately a distinct key).
- ``MENTORAPP_ESPO_BASE_URL`` — EspoCRM's ``api/v1`` base URL for the
  transport.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from mentorapp.access import (
    CredentialCipher,
    IdentityBridge,
    SessionManagement,
    StoredIdentityBridge,
    StoredSessionStore,
    StoredTokenActionStore,
    TokenActionService,
    VerifiedIdentity,
)
from mentorapp.api.deps import get_session
from mentorapp.api.routers.auth import (
    CredentialVerifier,
    ForgotPasswordFlow,
    get_credential_verifier,
    get_forgot_password_flow,
    get_session_management,
    get_token_actions,
)
from mentorapp.crm.espo import EspoAuthGateway, EspoResponse, EspoTransport

_SessionDep = Annotated[Session, Depends(get_session)]


def _decoded_key(name: str) -> bytes:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set; the auth backend cannot serve requests.")
    try:
        return base64.urlsafe_b64decode(value.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise RuntimeError(f"{name} is not valid urlsafe base64.") from exc


class UrllibEspoTransport:
    """:class:`EspoTransport` over stdlib urllib — the WTK-010 HTTP seam.

    Stdlib-first (boring-dependency policy): the app has no other runtime
    HTTP need, so no client library is adopted for one seam. An HTTP error
    status IS an answer and is returned, not raised — status handling is
    gateway policy; only a no-response failure propagates, which the gateway
    maps to ``CrmUnavailableError``.
    """

    def __init__(self, base_url: str, *, timeout_seconds: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def send(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> EspoResponse:
        url = f"{self._base_url}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(dict(params))}"
        body = _dumps(json).encode() if json is not None else None
        request_headers = dict(headers)
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return EspoResponse(response.status, _payload(response.read()))
        except urllib.error.HTTPError as exc:
            return EspoResponse(exc.code, _payload(exc.read()))


def _dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload)


def _payload(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return None


def get_espo_transport() -> EspoTransport:
    """Provide the Espo HTTP transport; tests override this with a fake."""
    base_url = os.environ.get("MENTORAPP_ESPO_BASE_URL")
    if not base_url:
        raise RuntimeError(
            "MENTORAPP_ESPO_BASE_URL is not set; the CRM verifier cannot serve requests."
        )
    return UrllibEspoTransport(base_url)


_TransportDep = Annotated[EspoTransport, Depends(get_espo_transport)]


@dataclass(frozen=True)
class EspoCredentialVerifier:
    """:class:`CredentialVerifier`: Espo verification composed with the identity bridge.

    The one place the two verified-identity shapes meet in production: the
    gateway proves the credentials against Espo, the bridge finds-or-provisions
    the ``appUser`` and carries the CRM credential through. Typed refusals
    (rejected/unavailable) propagate untouched — the router owns their mapping.
    """

    gateway: EspoAuthGateway
    bridge: IdentityBridge

    def verify(self, login_name: str, password: str) -> VerifiedIdentity:
        return self.bridge.resolve(self.gateway.verify(login_name, password))


@dataclass(frozen=True)
class EspoForgotPasswordFlow:
    """:class:`ForgotPasswordFlow` forwarding to Espo's own connected recovery flow."""

    gateway: EspoAuthGateway

    def initiate(self, login_name: str, email_address: str) -> None:
        self.gateway.request_password_reset(login_name, email_address)


def provide_session_management(session: _SessionDep) -> SessionManagement:
    """The production ``get_session_management``: stored sessions, sealed credentials."""
    cipher = CredentialCipher(_decoded_key("MENTORAPP_CREDENTIAL_KEY"))
    return SessionManagement(StoredSessionStore(session, cipher=cipher))


def provide_credential_verifier(
    session: _SessionDep, transport: _TransportDep
) -> CredentialVerifier:
    """The production ``get_credential_verifier``: Espo + the stored identity bridge."""
    return EspoCredentialVerifier(EspoAuthGateway(transport), StoredIdentityBridge(session))


def provide_token_actions(session: _SessionDep) -> TokenActionService:
    """The production ``get_token_actions``: stored tokens under the signing key."""
    return TokenActionService(
        StoredTokenActionStore(session),
        signing_key=_decoded_key("MENTORAPP_TOKEN_SIGNING_KEY"),
    )


def provide_forgot_password_flow(transport: _TransportDep) -> ForgotPasswordFlow:
    """The production ``get_forgot_password_flow``: Espo's connected recovery flow."""
    return EspoForgotPasswordFlow(EspoAuthGateway(transport))


def install_auth_wiring(app: FastAPI) -> None:
    """Bind the production auth backends onto the router's provider seams.

    Uses ``dependency_overrides`` keyed on the original fail-loud providers —
    the one binding mechanism the router documents — so a later override (a
    test, a deployment variant) replaces exactly one seam without unwinding
    the rest.
    """
    app.dependency_overrides[get_session_management] = provide_session_management
    app.dependency_overrides[get_credential_verifier] = provide_credential_verifier
    app.dependency_overrides[get_token_actions] = provide_token_actions
    app.dependency_overrides[get_forgot_password_flow] = provide_forgot_password_flow
