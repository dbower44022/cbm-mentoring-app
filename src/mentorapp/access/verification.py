"""The wired login seams: CRM proof composed into app-side outcomes (WTK-010).

The auth endpoints (``api/routers/auth.py``) depend on two protocols —
``CredentialVerifier`` (credentials → app-side
:class:`~mentorapp.access.identity.VerifiedIdentity`) and
``ForgotPasswordFlow``. This module is where those are produced from the
CRM-agnostic seams of :mod:`mentorapp.crm.auth`:

- :class:`CrmCredentialVerifier` composes a
  :class:`~mentorapp.crm.auth.CredentialVerification` plug with the
  :class:`~mentorapp.access.identity.IdentityBridge` — CRM proof, then the
  one CRM→app identity resolve. It stays pluggable on both sides (Espo
  binding or fake; in-memory or persistent bridge), and the typed CRM
  outcomes (:class:`~mentorapp.crm.auth.CredentialsRejectedError`,
  :class:`~mentorapp.crm.auth.CrmUnavailableError`) pass through untouched —
  the endpoint's rejected/unavailable distinction is decided at the CRM seam,
  re-deciding it here would let the two drift.
- :class:`CrmForgotPasswordFlow` adapts the
  :class:`~mentorapp.crm.auth.ForgotPassword` seam onto the endpoint's
  ``initiate`` vocabulary; the uniform found-or-not outcome is the CRM
  seam's contract and is preserved, not re-implemented.
"""

from __future__ import annotations

from dataclasses import dataclass

from mentorapp.access.identity import IdentityBridge, VerifiedIdentity
from mentorapp.crm.auth import CredentialVerification, ForgotPassword


@dataclass(frozen=True)
class CrmCredentialVerifier:
    """The composed REQ-008 verifier: CRM verification, then the identity bridge."""

    verification: CredentialVerification
    bridge: IdentityBridge

    def verify(self, login_name: str, password: str) -> VerifiedIdentity:
        """Prove the credentials against the CRM and resolve the app identity.

        Returns only on proof; :class:`~mentorapp.crm.auth.CredentialsRejectedError`
        and :class:`~mentorapp.crm.auth.CrmUnavailableError` propagate from the
        CRM seam for the endpoint's error mapping.
        """
        return self.bridge.resolve(self.verification.verify(login_name, password))


@dataclass(frozen=True)
class CrmForgotPasswordFlow:
    """The endpoint's reset flow, delegating to the CRM's connected recovery."""

    forgot_password: ForgotPassword

    def initiate(self, login_name: str, email_address: str) -> None:
        """Forward the reset request; uniform ``None`` whether or not it matched.

        :class:`~mentorapp.crm.auth.CrmUnavailableError` propagates only when
        the CRM cannot take the request at all.
        """
        self.forgot_password.request_password_reset(login_name, email_address)
