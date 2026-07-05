"""UI layer: executable screen and window-behavior designs (PI-002 renders them).

- :mod:`~mentorapp.ui.auth_flows` — login & session UI (WTK-005): the three
  credential screens, the educate-voice outcome messages over the WTK-003
  taxonomy, and the cross-window in-place re-auth controller with
  unsaved-work preservation.
"""

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
    EducateMessage,
    FieldControl,
    InMemorySessionChannel,
    ScreenField,
    SessionLoggedOut,
    SessionRevived,
    UnsavedWorkGuardError,
    WindowPhase,
    WindowSessionController,
    login_failure_message,
)

__all__ = [
    "FORGOT_PASSWORD_SCREEN",
    "LOGIN_SCREEN",
    "REAUTH_PROMPT",
    "REAUTH_SCREEN",
    "REAUTH_WRONG_USER",
    "RESET_REQUESTED",
    "SESSION_ENDED",
    "SIGN_IN_CRM_UNAVAILABLE",
    "SIGN_IN_REJECTED",
    "AuthScreen",
    "EducateMessage",
    "FieldControl",
    "InMemorySessionChannel",
    "ScreenField",
    "SessionLoggedOut",
    "SessionRevived",
    "UnsavedWorkGuardError",
    "WindowPhase",
    "WindowSessionController",
    "login_failure_message",
]
