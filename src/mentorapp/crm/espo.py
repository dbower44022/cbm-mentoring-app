"""The EspoCRM binding of the CRM authentication seams (WTK-003).

:class:`EspoAuthGateway` plugs all three seams from :mod:`mentorapp.crm.auth`
— :class:`~mentorapp.crm.auth.CredentialVerification`,
:class:`~mentorapp.crm.auth.ForgotPassword` and
:class:`~mentorapp.crm.auth.CrmAccess` — onto EspoCRM's REST API, over an
injected :class:`EspoTransport` (the ``FeedPushTransport`` pattern: WTK-010
supplies the real HTTP transport; tests plug fakes).

How each seam maps onto Espo:

- **Login** is ``GET App/user`` with ``Espo-Authorization`` carrying the
  base64 ``username:password`` pair and ``Espo-Authorization-Create-Token``
  set, so the same exchange that proves the credentials issues the per-user
  auth token. The password is used for that one request and discarded — the
  :class:`~mentorapp.crm.auth.CrmUserCredential` holds the issued token. A
  success without a token is treated as :class:`CrmUnavailableError`, not
  papered over by keeping the password.
- **ForgotPassword** is ``POST User/passwordChangeRequest``: Espo sends its
  own recovery email. Espo answers 404 when nothing matches; the gateway
  maps found and not-found to the same ``None`` (anti-enumeration, logged).
- **CrmAccess** sends ``Espo-Authorization`` as ``username:token`` with
  ``Espo-Authorization-By-Token`` set, so every user-initiated operation
  executes as the user's own Espo account. A 401 here means Espo dropped
  the token (:class:`~mentorapp.crm.auth.CrmCredentialExpiredError` — the
  session layer re-establishes the login); other 4xx are operation-level
  refusals (:class:`EspoOperationRejectedError`) the calling feature handles.
"""

from __future__ import annotations

from base64 import b64encode
from collections.abc import Mapping
from typing import Any, NamedTuple, Protocol

from mentorapp.crm.auth import (
    CredentialsRejectedError,
    CrmCredentialExpiredError,
    CrmUnavailableError,
    CrmUserCredential,
    VerifiedIdentity,
)
from mentorapp.observability import get_logger

logger = get_logger(__name__)

_LOGIN_PATH = "App/user"
_PASSWORD_RESET_PATH = "User/passwordChangeRequest"


class EspoResponse(NamedTuple):
    """One decoded Espo answer: HTTP status plus the JSON payload (or ``None``)."""

    status_code: int
    payload: Any


class EspoTransport(Protocol):
    """Where Espo API requests go — the one HTTP seam of the binding.

    ``send`` performs one request against Espo's ``api/v1`` base and returns
    the response for ANY HTTP status (status handling is gateway policy, not
    transport policy); it raises only when no response could be obtained at
    all (network failure), which the gateway maps to
    :class:`~mentorapp.crm.auth.CrmUnavailableError`.
    """

    def send(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> EspoResponse: ...


class EspoOperationRejectedError(Exception):
    """Espo refused one user-initiated operation (4xx other than a dropped token).

    Not an authentication outcome: the credential still stands, the specific
    operation was refused (forbidden record, validation failure, missing
    entity). ``status_code`` and ``payload`` carry Espo's answer for the
    calling feature to translate.
    """

    def __init__(self, status_code: int, payload: Any) -> None:
        super().__init__(f"EspoCRM rejected the operation (HTTP {status_code})")
        self.status_code = status_code
        self.payload = payload


def _authorization(username: str, secret: str) -> str:
    return b64encode(f"{username}:{secret}".encode()).decode("ascii")


class EspoAuthGateway:
    """The Espo plug for CredentialVerification, ForgotPassword and CrmAccess."""

    def __init__(self, transport: EspoTransport) -> None:
        self._transport = transport

    def _send(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> EspoResponse:
        # The one containment point for transport failures: anything the
        # transport raises means "no answer from the CRM" — logged, then
        # surfaced as CrmUnavailableError with the cause attached, never swallowed.
        try:
            return self._transport.send(method, path, headers=headers, params=params, json=json)
        except Exception as exc:
            logger.exception(
                "EspoCRM transport failure",
                extra={"context": {"method": method, "path": path}},
            )
            raise CrmUnavailableError("EspoCRM did not answer") from exc

    def verify(self, username: str, password: str) -> VerifiedIdentity:
        """Prove ``username``/``password`` against Espo; capture the user token.

        Raises :class:`CredentialsRejectedError` on Espo's 401 and
        :class:`CrmUnavailableError` on any answer that is neither a clean
        acceptance nor a clean refusal — including a success that carries no
        token or no user, which cannot become a usable login.
        """
        response = self._send(
            "GET",
            _LOGIN_PATH,
            headers={
                "Espo-Authorization": _authorization(username, password),
                "Espo-Authorization-Create-Token": "true",
            },
        )
        if response.status_code == 401:
            raise CredentialsRejectedError("EspoCRM refused the credentials")
        if response.status_code != 200:
            logger.error(
                "EspoCRM login exchange failed",
                extra={"context": {"statusCode": response.status_code}},
            )
            raise CrmUnavailableError(
                f"EspoCRM login exchange failed (HTTP {response.status_code})"
            )
        payload = response.payload if isinstance(response.payload, Mapping) else {}
        user = payload.get("user")
        token = payload.get("token")
        if not isinstance(user, Mapping) or not user.get("id") or not token:
            logger.error(
                "EspoCRM login succeeded without a usable user/token payload",
                extra={
                    "context": {"hasUser": isinstance(user, Mapping), "hasToken": bool(token)}
                },
            )
            raise CrmUnavailableError("EspoCRM login answer carried no usable user/token")
        return VerifiedIdentity(
            crm_user_id=str(user["id"]),
            username=str(user.get("userName") or username),
            display_name=str(user.get("name") or username),
            email_address=(
                str(user["emailAddress"]) if user.get("emailAddress") is not None else None
            ),
            credential=CrmUserCredential(username=username, secret=str(token)),
        )

    def request_password_reset(self, username: str, email_address: str) -> None:
        """Forward recovery to Espo's own flow; uniform outcome by design.

        Espo answers 404 when the pair matches no account — mapped to the
        same ``None`` as a match (anti-enumeration; the miss is logged for
        operators). Raises :class:`CrmUnavailableError` when Espo cannot take the
        request.
        """
        response = self._send(
            "POST",
            _PASSWORD_RESET_PATH,
            headers={},
            json={"userName": username, "emailAddress": email_address},
        )
        if response.status_code == 404:
            logger.info(
                "EspoCRM password reset matched no account",
                extra={"context": {"path": _PASSWORD_RESET_PATH}},
            )
            return
        if response.status_code != 200:
            logger.error(
                "EspoCRM password reset request failed",
                extra={"context": {"statusCode": response.status_code}},
            )
            raise CrmUnavailableError(
                f"EspoCRM password reset request failed (HTTP {response.status_code})"
            )

    def execute(
        self,
        credential: CrmUserCredential,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> Any:
        """One CRM operation as the user's own Espo account; returns the payload.

        Raises :class:`CrmCredentialExpiredError` on 401 (Espo dropped the token),
        :class:`EspoOperationRejectedError` on any other 4xx, and
        :class:`CrmUnavailableError` on 5xx or no answer.
        """
        response = self._send(
            method,
            path,
            headers={
                "Espo-Authorization": _authorization(credential.username, credential.secret),
                "Espo-Authorization-By-Token": "true",
            },
            params=params,
            json=json,
        )
        if response.status_code == 401:
            raise CrmCredentialExpiredError("EspoCRM no longer honours the session token")
        if response.status_code >= 500:
            logger.error(
                "EspoCRM operation failed server-side",
                extra={
                    "context": {
                        "method": method,
                        "path": path,
                        "statusCode": response.status_code,
                    }
                },
            )
            raise CrmUnavailableError(f"EspoCRM operation failed (HTTP {response.status_code})")
        if response.status_code >= 400:
            raise EspoOperationRejectedError(response.status_code, response.payload)
        return response.payload
