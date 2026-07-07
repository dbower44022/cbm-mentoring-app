"""``POST /auth/*`` — login, logout, in-place re-auth, forgot-password, action links.

The authentication and access API surface (REQ-005, REQ-007, REQ-008),
designed over the access-layer processes:

- The browser only ever holds the opaque session reference minted by
  :class:`SessionManagement`; expiry, roles, and revocation live server-side,
  so nothing security-relevant is parsed out of client input.
- Every refusal is generic where genericity protects something: unknown
  login name and wrong password are one indistinguishable
  ``invalidCredentials`` response; forgot-password answers identically
  whether or not the account exists; action-token redemption never names the
  identity it runs as. Two outcomes are deliberately DISTINCT because they
  disclose nothing enumerable: a CRM outage (``crmUnavailable``, 503 — never
  a wrong password) and a re-auth identity mismatch
  (``reauthIdentityMismatch`` — the session's owner is already on screen).
  The handlers in ``mentorapp.api.errors`` own the refusal envelopes — no
  endpoint here builds an error response.
- Credential verification stays behind the pluggable
  :class:`CredentialVerifier` seam (REQ-008: the ESPO integration today,
  SSO/MFA later) — this router never sees how a password is checked, only
  whether it verified.

Backends are FastAPI dependencies with fail-loud defaults (the ``deps.py``
pattern): the production wiring lands with PI-001's backend work; tests and
deployments bind via ``app.dependency_overrides``.
"""

from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from mentorapp.access import (
    IdentityMismatchError,
    ReauthRequiredError,
    SessionManagement,
    SessionNotFoundError,
    TokenActionService,
    VerifiedIdentity,
)
from mentorapp.api.deps import get_session_management
from mentorapp.api.envelope import Envelope, ok
from mentorapp.api.errors import (
    CrmUnavailableError,
    InvalidCredentialsError,
    ReauthIdentityMismatchError,
)

# The crm-layer outcome and the api-layer error share a name on purpose (one
# concept, two layers); the alias keeps this module able to catch one and
# raise the other.
from mentorapp.crm.auth import CredentialsRejectedError
from mentorapp.crm.auth import CrmUnavailableError as CrmAuthUnavailableError
from mentorapp.observability import get_logger
from mentorapp.ui.auth_flows import (
    FORGOT_PASSWORD_SCREEN,
    LOGIN_SCREEN,
    REAUTH_PROMPT,
    REAUTH_SCREEN,
    REAUTH_WRONG_USER,
    RESET_REQUESTED,
    SESSION_ENDED,
    SIGN_IN_CRM_UNAVAILABLE,
    SIGN_IN_REJECTED,
    AuthScreen,
)

router = APIRouter(prefix="/auth")
log = get_logger(__name__)


class CredentialVerifier(Protocol):
    """The REQ-008 verification seam: prove who a credential pair belongs to.

    Returns the access-layer :class:`VerifiedIdentity` (post-bridge — CRM
    verification and the ``access/identity.py`` resolve composed) on proof,
    and raises :class:`~mentorapp.crm.auth.CredentialsRejectedError` or
    :class:`~mentorapp.crm.auth.CrmUnavailableError` otherwise. An earlier
    None-on-any-failure protocol conflated outage with rejection, which the
    design forbids — a CRM outage must never present as a wrong password.
    Anti-enumeration lives in the ERROR MAPPING below, not in erasing the
    cause.
    """

    def verify(self, login_name: str, password: str) -> VerifiedIdentity: ...


class ForgotPasswordFlow(Protocol):
    """Start a password reset — the CRM's own connected flow, enumeration-proof.

    The CRM owns the credentials, so recovery is the CRM's: the
    implementation forwards EspoCRM's ``User/passwordChangeRequest``
    (``crm/espo.py``) and the CRM sends its recovery email — the app mints NO
    token and sends NO email. It MUST behave identically whether or not the
    account exists (no exception, no timing shortcut), because the endpoint's
    constant response is only as generic as this seam is; it raises
    :class:`~mentorapp.crm.auth.CrmUnavailableError` only when the CRM cannot
    take the request at all (→ 503).
    """

    def initiate(self, login_name: str, email_address: str) -> None: ...


# get_session_management moved to mentorapp.api.deps (FND-909 D9): the whole
# API resolves the acting user through it now, not just this router. Imported
# above so existing override sites keyed on this module keep binding the same
# provider object.


def get_credential_verifier() -> CredentialVerifier:
    """Provide the REQ-008 verifier; deployments and tests override this."""
    raise RuntimeError("credential verification is not wired; override get_credential_verifier")


def get_token_actions() -> TokenActionService:
    """Provide the action-token service; deployments and tests override this."""
    raise RuntimeError("token actions are not wired; override get_token_actions")


def get_forgot_password_flow() -> ForgotPasswordFlow:
    """Provide the reset flow; deployments and tests override this."""
    raise RuntimeError(
        "the forgot-password flow is not wired; override get_forgot_password_flow"
    )


class LoginBody(BaseModel):
    """``POST /auth/login`` body — the only surface where credentials travel."""

    login_name: str = Field(alias="loginName")
    password: str


class LogoutBody(BaseModel):
    """``POST /auth/logout`` body: the reference whose session ends."""

    session_reference: str = Field(alias="sessionReference")


class ReauthBody(BaseModel):
    """``POST /auth/reauth`` body: the held reference plus fresh credentials."""

    session_reference: str = Field(alias="sessionReference")
    login_name: str = Field(alias="loginName")
    password: str


class ForgotPasswordBody(BaseModel):
    """``POST /auth/forgot-password`` body: identifiers only, never a password.

    Both identifiers travel because EspoCRM's ``passwordChangeRequest``
    requires the username+email pair — matching WTK-005's
    ``FORGOT_PASSWORD_SCREEN``.
    """

    login_name: str = Field(alias="loginName")
    email_address: str = Field(alias="emailAddress")


class RedeemBody(BaseModel):
    """``POST /auth/actions/{action}/redeem`` body: the emailed link token."""

    action_token: str = Field(alias="actionToken")


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _screen_payload(screen: AuthScreen) -> dict[str, object]:
    # Field names travel camelCase (the wire-name standard), matching the
    # /auth body aliases the same fields post back to (emailAddress).
    return {
        "key": screen.key,
        "title": screen.title,
        "fields": [
            {
                "name": _camel(screen_field.name),
                "label": screen_field.label,
                "control": screen_field.control.value,
                "readOnly": screen_field.read_only,
            }
            for screen_field in screen.fields
        ],
        "submitLabel": screen.submit_label,
        "links": list(screen.links),
        "enterSubmits": screen.enter_submits,
    }


@router.get("/screens")
def get_auth_screens() -> Envelope:
    """Serve the WTK-005 credential screens and educate messages verbatim.

    The shell renders these payloads as-is (DEC-080: view-models are served,
    never re-decided client-side), so the auth_flows declarations stay the one
    canonical home for field order, masking, and the educate wording — the
    outage message stays distinct from the rejection message by construction.
    Unauthenticated by design: the login screen renders before any session
    exists, and nothing here is secret. Always succeeds.
    """
    return ok(
        data={
            "screens": {
                LOGIN_SCREEN.key: _screen_payload(LOGIN_SCREEN),
                FORGOT_PASSWORD_SCREEN.key: _screen_payload(FORGOT_PASSWORD_SCREEN),
                REAUTH_SCREEN.key: _screen_payload(REAUTH_SCREEN),
            },
            "messages": {
                "signInRejected": SIGN_IN_REJECTED.as_payload(),
                "signInCrmUnavailable": SIGN_IN_CRM_UNAVAILABLE.as_payload(),
                "resetRequested": RESET_REQUESTED.as_payload(),
                "reauthPrompt": REAUTH_PROMPT.as_payload(),
                "reauthWrongUser": REAUTH_WRONG_USER.as_payload(),
                "sessionEnded": SESSION_ENDED.as_payload(),
            },
        }
    )


def _session_payload(reference: str, identity: VerifiedIdentity) -> dict[str, object]:
    # The caller learns its own identity and roles plus the one opaque
    # reference — nothing about the server-side record (expiry, state).
    return {
        "sessionReference": reference,
        "userID": str(identity.user_id),
        "roleNames": sorted(identity.role_names),
    }


@router.post("/login")
def login(
    body: LoginBody,
    sessions: Annotated[SessionManagement, Depends(get_session_management)],
    verifier: Annotated[CredentialVerifier, Depends(get_credential_verifier)],
) -> Envelope:
    """Verify credentials and open a session; return the opaque reference.

    Rejection is the one generic ``invalidCredentials`` 401 regardless of
    which credential was wrong; a CRM outage is the distinct
    ``crmUnavailable`` 503 — the credentials were never judged, and that
    must never read as a wrong password. The refusal is logged without the
    login name: a failed login field routinely contains a password typed
    into the wrong box, so the attempted identifier never reaches the log
    stream.
    """
    try:
        identity = verifier.verify(body.login_name, body.password)
    except CredentialsRejectedError:
        log.info("login refused")
        raise InvalidCredentialsError() from None
    except CrmAuthUnavailableError as exc:
        raise CrmUnavailableError() from exc
    reference, _record = sessions.establish(identity)
    return ok(data=_session_payload(reference, identity))


@router.post("/logout")
def logout(
    body: LogoutBody,
    sessions: Annotated[SessionManagement, Depends(get_session_management)],
) -> Envelope:
    """End the shared session record so every window fails closed (REQ-005).

    Always answers the same success envelope: logging out an unknown,
    already-rotated, or already-ended reference reveals nothing about which
    references exist, and the caller's goal state ("not signed in") holds
    either way.
    """
    try:
        sessions.logout(body.session_reference)
    except SessionNotFoundError:
        log.info("logout presented an unknown session reference")
    return ok(data={"loggedOut": True})


@router.post("/reauth")
def reauth(
    body: ReauthBody,
    sessions: Annotated[SessionManagement, Depends(get_session_management)],
    verifier: Annotated[CredentialVerifier, Depends(get_credential_verifier)],
) -> Envelope:
    """Re-authenticate in place: one re-login restores every window (REQ-005).

    Serves both the ``reauthRequired`` challenge (revive the expired session,
    same identity, rotated secret) and proactive refresh of a still-active
    session. Credentials are checked before the session is touched, so the
    ``invalidCredentials`` refusal is identical whether or not the reference
    is live. A dead session (ended, or grace lapsed) answers the generic
    ``unauthenticated`` 401 — fresh login required. Presenting another
    user's VALID credentials is the distinct ``reauthIdentityMismatch``
    refusal: the session's owner is already on the caller's screen, so the
    precise code discloses nothing and the session stays revivable by the
    right user.
    """
    try:
        identity = verifier.verify(body.login_name, body.password)
    except CredentialsRejectedError:
        log.info("reauth refused")
        raise InvalidCredentialsError() from None
    except CrmAuthUnavailableError as exc:
        raise CrmUnavailableError() from exc
    try:
        session_id = sessions.resolve(body.session_reference).session_id
    except ReauthRequiredError as exc:
        # The expired-but-revivable state this endpoint exists for.
        session_id = exc.session_id
    try:
        reference = sessions.reauthenticate(session_id, identity)
    except IdentityMismatchError:
        raise ReauthIdentityMismatchError() from None
    return ok(data=_session_payload(reference, identity))


@router.post("/forgot-password")
def forgot_password(
    body: ForgotPasswordBody,
    flow: Annotated[ForgotPasswordFlow, Depends(get_forgot_password_flow)],
) -> Envelope:
    """Start a password reset; the answer never says whether the account exists.

    The seam forwards to the CRM's own connected recovery flow (see
    :class:`ForgotPasswordFlow`); this endpoint's whole contract is the
    constant response. Reset links land the recipient on the CRM's own flow,
    not app action-token redemption — no app credential or session state
    changes here. Only a CRM outage breaks the constant answer (503, which
    says nothing about any account).
    """
    try:
        flow.initiate(body.login_name, body.email_address)
    except CrmAuthUnavailableError as exc:
        raise CrmUnavailableError() from exc
    return ok(data={"resetRequestAccepted": True})


@router.post("/actions/{action_name}/redeem")
def redeem_action(
    action_name: str,
    body: RedeemBody,
    tokens: Annotated[TokenActionService, Depends(get_token_actions)],
) -> Envelope:
    """Spend one use of an emailed action link (REQ-007).

    The path names the action this call serves and the service refuses a
    token minted for any other action — the binding check is server-side,
    never client-honored. The response carries no identity: downstream
    feature endpoints consume the redeemed record server-side; the link
    holder learns only that the action went through and what remains of the
    use budget. Refusals are the generic 403 token envelope.
    """
    record = tokens.redeem(body.action_token, expected_action=action_name)
    return ok(
        data={
            "actionName": record.action_name,
            "usesRemaining": record.max_uses - record.use_count,
        }
    )
