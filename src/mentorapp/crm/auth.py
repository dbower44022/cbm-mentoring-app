"""CRM authentication seams: verification, forgot-password, and user-as-user access.

The authentication design against the CRM system of record (WTK-003), decided
once for every login path:

- **The app never keeps passwords.** Login hands the submitted credentials to
  a :class:`CredentialVerification` plug, which proves them against the CRM
  and produces a :class:`VerifiedIdentity` — the only object the session
  layer (WTK-002) may mint an ``authSession`` from. ``appUser`` anchors to
  the CRM via ``crmUserID`` (``storage/auth.py``), so
  ``VerifiedIdentity.crm_user_id`` is the find-or-provision join point.
- **Two failure outcomes, never conflated.** :class:`CredentialsRejectedError`
  means the CRM said no; :class:`CrmUnavailableError` means the CRM could not
  answer. A CRM outage must never present as a wrong password — the login UI
  and its messaging (WTK-005) branch on exactly this distinction.
- **ForgotPassword is the connected CRM flow.** The CRM owns the credentials,
  so recovery is the CRM's own: the app forwards the request and the CRM
  sends its recovery email. The app-local ``actionToken`` table covers only
  app-minted tokens (invites, magic links) — never CRM password resets.
  The outcome is deliberately uniform (``None`` whether or not the account
  exists) so the endpoint cannot be used to enumerate CRM accounts.
- **User-initiated CRM operations run as the user.** Verification captures a
  session-scoped :class:`CrmUserCredential` (a CRM-issued token — not the
  password), and :class:`CrmAccess` executes every user-initiated operation
  under it, never under a shared service account, so the CRM's own audit
  trail attributes each write to the human who made it. When the CRM stops
  honouring the token, :class:`CrmCredentialExpiredError` tells the session layer
  the login must be re-established.

The seams are ``Protocol``s (the ``FeedPushTransport`` pattern): the EspoCRM
binding in :mod:`mentorapp.crm.espo` is one plug; tests and local runs plug
fakes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


class CrmAuthError(Exception):
    """Base of every CRM authentication outcome raised by these seams."""


class CredentialsRejectedError(CrmAuthError):
    """The CRM answered and refused the credentials."""


class CrmUnavailableError(CrmAuthError):
    """The CRM could not answer (outage, network, malformed exchange).

    Distinct from :class:`CredentialsRejectedError` by design: an outage must
    never read as a wrong password. The underlying failure rides along as
    ``__cause__``.
    """


class CrmCredentialExpiredError(CrmAuthError):
    """The CRM stopped honouring a previously captured user credential.

    The session layer treats this as "re-establish the login", never as a
    fresh-credentials rejection.
    """


@dataclass(frozen=True)
class CrmUserCredential:
    """The session-scoped proof a user holds against the CRM.

    ``secret`` is the CRM-issued token captured at verification — never the
    user's password — and is excluded from ``repr`` so credentials can never
    leak through logging or error reporting.
    """

    username: str
    secret: str = field(repr=False)


@dataclass(frozen=True)
class VerifiedIdentity:
    """What a successful credential verification proves.

    ``crm_user_id`` is the CRM's own identifier — the join point to
    ``appUser.crmUserID``. ``credential`` rides along because the same
    exchange that proves the identity issues the user's CRM token; the
    session layer stores it session-scoped for :class:`CrmAccess`.
    """

    crm_user_id: str
    username: str
    display_name: str
    email_address: str | None
    credential: CrmUserCredential


class CredentialVerification(Protocol):
    """The pluggable login seam: submitted credentials → verified identity.

    ``verify`` raises :class:`CredentialsRejectedError` when the CRM refuses the
    pair and :class:`CrmUnavailableError` when it cannot answer; it returns only
    on proof.
    """

    def verify(self, username: str, password: str) -> VerifiedIdentity: ...


class ForgotPassword(Protocol):
    """The connected recovery seam: forward the request, the CRM does the rest.

    ``request_password_reset`` returns ``None`` uniformly — account found or
    not — and raises :class:`CrmUnavailableError` only when the CRM cannot take
    the request at all.
    """

    def request_password_reset(self, username: str, email_address: str) -> None: ...


class CrmAccess(Protocol):
    """User-initiated CRM operations, executed as the user's own CRM account.

    ``execute`` performs one CRM API operation under ``credential`` and
    returns the CRM's decoded payload. It raises
    :class:`CrmCredentialExpiredError` when the CRM no longer honours the
    credential and :class:`CrmUnavailableError` when the CRM cannot answer;
    operation-level refusals surface as the binding's own typed error.
    """

    def execute(
        self,
        credential: CrmUserCredential,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> Any: ...
