"""Login & session UI design: screens, messaging, cross-window re-auth (WTK-005).

The UI-layer design over the WTK-002 session process and the WTK-003
verification seams, decided once for every window the app opens. No frontend
shell exists yet (PI-002), so the design is executable surface the shell
renders verbatim:

- **Three credential screens, declared not hand-built.** ``LOGIN_SCREEN``,
  ``FORGOT_PASSWORD_SCREEN``, and ``REAUTH_SCREEN`` are frozen
  :class:`AuthScreen` definitions — the shell renders them; nothing about
  field order, masking, or affordances is re-decided per page.
- **Outcome messaging branches on the WTK-003 taxonomy, exactly.** A CRM
  outage must never read as a wrong password (``crm/auth.py``), so
  :func:`login_failure_message` maps :class:`CredentialsRejectedError` and
  :class:`CrmUnavailableError` to distinct educate-voice messages, and the
  rejection message never discloses whether the account exists or which
  field was wrong (anti-enumeration, matching the uniform ForgotPassword
  outcome).
- **Re-auth happens in place, in every window.** When any request raises
  ``ReauthRequiredError`` (WTK-002's dirty-window guard), the window overlays
  ``REAUTH_SCREEN`` without navigating — unsaved work stays in the window
  behind the prompt — and holds outgoing writes instead of dropping them.
  One successful re-login broadcasts :class:`SessionRevived` on the
  same-user cross-window channel (the BroadcastChannel-class mechanism from
  the layout standard), so every sibling window dismisses its prompt and
  replays what it held.
- **Work is never lost silently.** An ended session (grace lapsed, or logout
  elsewhere) moves the window to ``ENDED`` with its unsaved work still
  rendered and an educate-voice explanation; leaving is always the user's
  explicit act, and explicit logout runs the dirty-window guard first.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from mentorapp.crm.auth import (
    CredentialsRejectedError,
    CrmAuthError,
    CrmUnavailableError,
)
from mentorapp.observability import get_logger

log = get_logger(__name__)


# --- Educate voice ------------------------------------------------------------


@dataclass(frozen=True)
class EducateMessage:
    """The app-wide message shape: what happened → why → what next."""

    what_happened: str
    why: str
    what_next: str

    def as_payload(self) -> dict[str, str]:
        """The one wire shape every endpoint serves an educate message in."""
        return {
            "whatHappened": self.what_happened,
            "why": self.why,
            "whatNext": self.what_next,
        }


# The rejection message deliberately names neither the failing field nor
# account existence — the login endpoint must not enumerate CRM accounts,
# matching ForgotPassword's uniform outcome (crm/auth.py).
SIGN_IN_REJECTED = EducateMessage(
    what_happened="Sign-in didn't go through.",
    why="The username and password combination wasn't accepted.",
    what_next="Check both fields and try again, or use 'Forgot password?' below.",
)

SIGN_IN_CRM_UNAVAILABLE = EducateMessage(
    what_happened="Sign-in couldn't be checked.",
    why=(
        "The CRM system that verifies accounts isn't reachable right now — "
        "your username and password were not judged."
    ),
    what_next=(
        "Wait a moment and try again. If this keeps happening, contact your administrator."
    ),
)

# Uniform whether or not the account exists: the confirmation is the same
# message in both cases, so the recovery form can't enumerate accounts either.
RESET_REQUESTED = EducateMessage(
    what_happened="Recovery request sent.",
    why="If an account matches what you entered, the CRM has emailed it a recovery link.",
    what_next="Check that account's email inbox, then sign in with the new password.",
)

REAUTH_PROMPT = EducateMessage(
    what_happened="Your session has expired.",
    why="Sessions end after a period of inactivity or a maximum lifetime.",
    what_next=(
        "Sign back in right here — your unsaved work is untouched, and one "
        "sign-in restores every open window."
    ),
)

REAUTH_WRONG_USER = EducateMessage(
    what_happened="That sign-in belongs to a different user.",
    why="An expired session can only be resumed by the user who opened it.",
    what_next=(
        "Sign back in as the user shown above, or log out to start a fresh "
        "session as someone else."
    ),
)

SESSION_ENDED = EducateMessage(
    what_happened="This session has ended.",
    why="It was logged out, or its re-sign-in window lapsed.",
    what_next=(
        "Your unsaved work is still on screen — copy anything you need, "
        "then sign in again to start a new session."
    ),
)


def login_failure_message(error: CrmAuthError) -> EducateMessage:
    """Map a WTK-003 authentication outcome to its educate-voice message.

    Returns :data:`SIGN_IN_REJECTED` only when the CRM positively refused the
    credentials; every other outcome — outage, expired credential, or an
    outcome subclass this design has never seen — presents as
    :data:`SIGN_IN_CRM_UNAVAILABLE`. Failing toward "not your fault" is
    deliberate: a message may only blame the user's input when the CRM
    actually judged it.
    """
    if isinstance(error, CredentialsRejectedError):
        return SIGN_IN_REJECTED
    if not isinstance(error, CrmUnavailableError):
        log.warning(
            "unmapped auth outcome presented as CRM-unavailable",
            extra={"context": {"errorType": type(error).__name__}},
        )
    return SIGN_IN_CRM_UNAVAILABLE


# --- Screen definitions -------------------------------------------------------


class FieldControl(enum.StrEnum):
    TEXT = "text"
    PASSWORD = "password"
    EMAIL = "email"


@dataclass(frozen=True)
class ScreenField:
    """One rendered input; ``read_only`` fields display but never take focus."""

    name: str
    label: str
    control: FieldControl
    read_only: bool = False


@dataclass(frozen=True)
class AuthScreen:
    """A credential screen the shell renders verbatim.

    Focus starts on the first editable field (forms standard). ``links`` are
    plain navigation affordances rendered under the submit action, in order.
    ``enter_submits`` is True on every credential screen — a MARKED DEVIATION
    from the forms standard's "Enter never submits a multi-field form",
    raised for the design gate: these are single-purpose screens where
    Enter-to-sign-in is the universal convention and no partial multi-field
    record commit is possible.
    """

    key: str
    title: str
    fields: tuple[ScreenField, ...]
    submit_label: str
    links: tuple[str, ...] = ()
    enter_submits: bool = True

    def first_focus(self) -> ScreenField:
        """The field focus starts on: the first editable one, never read-only."""
        return next(f for f in self.fields if not f.read_only)


LOGIN_SCREEN = AuthScreen(
    key="login",
    title="Sign in",
    fields=(
        ScreenField("username", "Username", FieldControl.TEXT),
        ScreenField("password", "Password", FieldControl.PASSWORD),
    ),
    submit_label="Sign in",
    links=("Forgot password?",),
)

# Recovery is the CRM's own connected flow (WTK-003): both identifiers are
# collected because EspoCRM's passwordChangeRequest requires the pair.
FORGOT_PASSWORD_SCREEN = AuthScreen(
    key="forgotPassword",
    title="Forgot password",
    fields=(
        ScreenField("username", "Username", FieldControl.TEXT),
        ScreenField("email_address", "Email address", FieldControl.EMAIL),
    ),
    submit_label="Send recovery email",
    links=("Back to sign in",),
)

# The username is shown but fixed: only the session's owner may revive it
# (SessionManagement.reauthenticate refuses any other verified identity).
REAUTH_SCREEN = AuthScreen(
    key="reauth",
    title="Sign back in",
    fields=(
        ScreenField("username", "Username", FieldControl.TEXT, read_only=True),
        ScreenField("password", "Password", FieldControl.PASSWORD),
    ),
    submit_label="Sign back in",
    links=("Log out instead",),
)


# --- Cross-window session behavior --------------------------------------------


class WindowPhase(enum.StrEnum):
    ACTIVE = "active"
    REAUTH_PROMPT = "reauthPrompt"
    ENDED = "ended"


@dataclass(frozen=True)
class SessionRevived:
    """Broadcast after one window's successful in-place re-auth (new reference)."""

    reference: str


@dataclass(frozen=True)
class SessionLoggedOut:
    """Broadcast on explicit logout: every window of the session ends."""


class UnsavedWorkGuardError(Exception):
    """Logout attempted over unsaved work without the dirty-guard confirmation."""


class InMemorySessionChannel:
    """Reference same-user cross-window channel (BroadcastChannel in the shell)."""

    def __init__(self) -> None:
        self._windows: list[WindowSessionController] = []

    def attach(self, window: WindowSessionController) -> None:
        self._windows.append(window)

    def broadcast(
        self, event: SessionRevived | SessionLoggedOut, sender: WindowSessionController
    ) -> None:
        for window in self._windows:
            if window is not sender:
                window.receive(event)


class WindowSessionController:
    """One window's session lifecycle: the shell delegates every transition here.

    The controller owns three invariants the standards demand: unsaved work
    is never cleared by any session transition, requests raised while re-auth
    is pending are held and replayed rather than dropped, and every
    transition that affects the whole session is broadcast so sibling windows
    move together.
    """

    def __init__(self, channel: InMemorySessionChannel, reference: str) -> None:
        self._channel = channel
        self.reference = reference
        self.phase = WindowPhase.ACTIVE
        self.unsaved_work: dict[str, str] = {}
        self.held_requests: list[str] = []
        channel.attach(self)

    def on_reauth_required(self) -> EducateMessage:
        """Overlay the re-auth prompt in place; idempotent per window.

        The overlay never navigates or reloads — the window's state (dirty
        edits, grid scroll/selection) stays alive behind it.
        """
        self.phase = WindowPhase.REAUTH_PROMPT
        return REAUTH_PROMPT

    def hold(self, request: str) -> None:
        """Queue a request raised while re-auth is pending, instead of dropping it."""
        self.held_requests.append(request)

    def complete_reauth(self, new_reference: str) -> list[str]:
        """Resume after this window's user re-authenticated; revive the siblings.

        Called with the reference ``SessionManagement.reauthenticate``
        returned. Returns this window's held requests for replay; sibling
        windows replay their own on the broadcast.
        """
        replay = self._resume(new_reference)
        self._channel.broadcast(SessionRevived(new_reference), sender=self)
        log.info(
            "window session revived; siblings notified",
            extra={"context": {"heldRequestCount": len(replay)}},
        )
        return replay

    def on_session_ended(self) -> EducateMessage:
        """Terminal: the session is gone, but the window keeps the user's work.

        Never clears ``unsaved_work`` — the ended screen renders it so the
        user can copy anything out before explicitly starting over.
        """
        self.phase = WindowPhase.ENDED
        return SESSION_ENDED

    def logout(self, *, discard_confirmed: bool = False) -> None:
        """Explicit logout, total across windows; the dirty guard runs first."""
        if self.unsaved_work and not discard_confirmed:
            raise UnsavedWorkGuardError("unsaved work requires explicit discard confirmation")
        self.phase = WindowPhase.ENDED
        self._channel.broadcast(SessionLoggedOut(), sender=self)

    def receive(self, event: SessionRevived | SessionLoggedOut) -> None:
        """Handle a sibling window's broadcast."""
        if isinstance(event, SessionRevived):
            # A revival only matters to windows still waiting on the prompt;
            # an already-ended window stays ended (its user saw SESSION_ENDED).
            if self.phase is WindowPhase.REAUTH_PROMPT:
                for request in self._resume(event.reference):
                    self.replay(request)
        else:
            self.on_session_ended()

    def replay(self, request: str) -> None:
        """Re-issue one held request; the shell overrides this with real I/O."""
        log.info("held request replayed", extra={"context": {"request": request}})

    def _resume(self, new_reference: str) -> list[str]:
        self.reference = new_reference
        self.phase = WindowPhase.ACTIVE
        replay, self.held_requests = self.held_requests, []
        return replay
