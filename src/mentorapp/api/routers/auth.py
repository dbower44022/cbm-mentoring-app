"""``POST /auth/*`` — login, logout, in-place re-auth, forgot-password, action links.

The authentication and access API surface (REQ-005, REQ-007, REQ-008),
designed over the access-layer processes:

- The browser only ever holds the opaque session reference minted by
  :class:`SessionManagement`; expiry, roles, and revocation live server-side,
  so nothing security-relevant is parsed out of client input.
- Every refusal is generic by construction: unknown login name, wrong
  password, and re-auth identity mismatch are one indistinguishable
  ``invalidCredentials`` response; forgot-password answers identically
  whether or not the account exists; action-token redemption never names the
  identity it runs as. The handlers in ``mentorapp.api.errors`` own the
  refusal envelopes — no endpoint here builds an error response.
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
    ReauthRequiredError,
    SessionManagement,
    SessionNotFoundError,
    TokenActionService,
    VerifiedIdentity,
)
from mentorapp.api.envelope import Envelope, ok
from mentorapp.api.errors import InvalidCredentialsError
from mentorapp.observability import get_logger

router = APIRouter(prefix="/auth")
log = get_logger(__name__)


class CredentialVerifier(Protocol):
    """The REQ-008 verification seam: prove who a credential pair belongs to.

    Returns the :class:`VerifiedIdentity` on success and ``None`` on ANY
    failure — the protocol deliberately cannot say why, so no caller can
    distinguish an unknown account from a wrong password.
    """

    def verify(self, login_name: str, password: str) -> VerifiedIdentity | None: ...


class ForgotPasswordFlow(Protocol):
    """Start a password reset for a login name, enumeration-proof by contract.

    Implementations own the account lookup, minting the single-use
    ``passwordReset`` action token (:class:`TokenActionService`), and
    enqueueing the reset email job — and MUST behave identically for unknown
    accounts (no exception, no timing shortcut), because the endpoint's
    constant response is only as generic as this seam is.
    """

    def initiate(self, login_name: str) -> None: ...


def get_session_management() -> SessionManagement:
    """Provide the session process; deployments and tests override this.

    Fail-loud like ``deps._engine``: an unwired auth backend must be a clear
    server error, never a silently permissive fallback.
    """
    raise RuntimeError("session management is not wired; override get_session_management")


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
    """``POST /auth/forgot-password`` body: only the login name, never a password."""

    login_name: str = Field(alias="loginName")


class RedeemBody(BaseModel):
    """``POST /auth/actions/{action}/redeem`` body: the emailed link token."""

    action_token: str = Field(alias="actionToken")


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

    Failure is the one generic ``invalidCredentials`` 401 regardless of
    cause. The refusal is logged without the login name: a failed login
    field routinely contains a password typed into the wrong box, so the
    attempted identifier never reaches the log stream.
    """
    identity = verifier.verify(body.login_name, body.password)
    if identity is None:
        log.info("login refused")
        raise InvalidCredentialsError()
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
    ``unauthenticated`` 401 — fresh login required; presenting another
    user's valid credentials reads exactly like a wrong password.
    """
    identity = verifier.verify(body.login_name, body.password)
    if identity is None:
        log.info("reauth refused")
        raise InvalidCredentialsError()
    try:
        session_id = sessions.resolve(body.session_reference).session_id
    except ReauthRequiredError as exc:
        # The expired-but-revivable state this endpoint exists for.
        session_id = exc.session_id
    reference = sessions.reauthenticate(session_id, identity)
    return ok(data=_session_payload(reference, identity))


@router.post("/forgot-password")
def forgot_password(
    body: ForgotPasswordBody,
    flow: Annotated[ForgotPasswordFlow, Depends(get_forgot_password_flow)],
) -> Envelope:
    """Start a password reset; the answer never says whether the account exists.

    The seam owns lookup, token mint, and the email job (see
    :class:`ForgotPasswordFlow`); this endpoint's whole contract is the
    constant response. Reset links land the recipient on action-token
    redemption — no credential or session state changes here.
    """
    flow.initiate(body.login_name)
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
