"""CredentialCipher: AEAD custody of the session-held CRM credential (FND-006).

The session owns the CRM-issued act-as-user token (WTK-003/FND-006): at rest
it lives on ``authSession.crmCredentialEncrypted`` encrypted under a server
key, never plaintext. AES-256-GCM with the owning session's ID as associated
data — a leaked column yields nothing without the key, and a valid ciphertext
cannot be replayed onto a different session row because the AAD binds each
credential to exactly one session.
"""

from __future__ import annotations

import base64
import json
import secrets
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mentorapp.crm.auth import CrmUserCredential

# AES-GCM's standard 96-bit nonce, prefixed to the ciphertext so the column
# is self-contained: one value to store, one to open.
_NONCE_LENGTH = 12


class CredentialSealError(Exception):
    """The stored ciphertext failed authentication — wrong key, session, or bytes.

    Treated as "no credential survives": the session layer re-captures a fresh
    CRM token at the next re-auth rather than trusting unverifiable bytes.
    """


class CredentialCipher:
    """Seal/open one CRM credential under the server key, bound to its session."""

    def __init__(self, key: bytes) -> None:
        """``key`` must be exactly 32 bytes (AES-256).

        A shorter key silently weakening the custody decision is a
        configuration error, not a choice — refused loudly at construction.
        """
        if len(key) != 32:
            raise ValueError("the credential key must be exactly 32 bytes (AES-256)")
        self._cipher = AESGCM(key)

    def seal(self, credential: CrmUserCredential, *, session_id: uuid.UUID) -> str:
        """Encrypt for ``crmCredentialEncrypted``: base64(nonce ‖ ciphertext)."""
        plaintext = json.dumps(
            {"username": credential.username, "secret": credential.secret}
        ).encode()
        nonce = secrets.token_bytes(_NONCE_LENGTH)
        sealed = nonce + self._cipher.encrypt(nonce, plaintext, session_id.bytes)
        return base64.b64encode(sealed).decode("ascii")

    def open(self, sealed: str, *, session_id: uuid.UUID) -> CrmUserCredential:
        """Decrypt a column value; raise :class:`CredentialSealError` on any tamper."""
        try:
            raw = base64.b64decode(sealed.encode("ascii"), validate=True)
            nonce, ciphertext = raw[:_NONCE_LENGTH], raw[_NONCE_LENGTH:]
            payload = json.loads(self._cipher.decrypt(nonce, ciphertext, session_id.bytes))
        except (ValueError, InvalidTag) as exc:
            raise CredentialSealError("stored credential failed authentication") from exc
        return CrmUserCredential(username=payload["username"], secret=payload["secret"])
