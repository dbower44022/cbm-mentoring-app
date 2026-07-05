"""Login & session UI design gate: screens, messaging, cross-window re-auth (WTK-005)."""

from __future__ import annotations

import pytest

from mentorapp.crm.auth import (
    CredentialsRejectedError,
    CrmAuthError,
    CrmCredentialExpiredError,
    CrmUnavailableError,
)
from mentorapp.ui import (
    FORGOT_PASSWORD_SCREEN,
    LOGIN_SCREEN,
    REAUTH_SCREEN,
    RESET_REQUESTED,
    SESSION_ENDED,
    SIGN_IN_CRM_UNAVAILABLE,
    SIGN_IN_REJECTED,
    FieldControl,
    InMemorySessionChannel,
    UnsavedWorkGuardError,
    WindowPhase,
    WindowSessionController,
    login_failure_message,
)

# --- Screen definitions -------------------------------------------------------


def test_login_screen_masks_password_and_offers_recovery() -> None:
    assert [f.name for f in LOGIN_SCREEN.fields] == ["username", "password"]
    password = LOGIN_SCREEN.fields[1]
    assert password.control is FieldControl.PASSWORD
    assert "Forgot password?" in LOGIN_SCREEN.links
    assert LOGIN_SCREEN.first_focus().name == "username"


def test_forgot_password_screen_collects_the_crm_required_pair() -> None:
    assert [f.name for f in FORGOT_PASSWORD_SCREEN.fields] == ["username", "email_address"]
    assert "Back to sign in" in FORGOT_PASSWORD_SCREEN.links


def test_reauth_screen_pins_the_username_and_focuses_the_password() -> None:
    username, password = REAUTH_SCREEN.fields
    assert username.read_only
    assert password.control is FieldControl.PASSWORD
    # Focus must skip the read-only owner display (tab stops: editable only).
    assert REAUTH_SCREEN.first_focus() is password
    assert "Log out instead" in REAUTH_SCREEN.links


# --- Outcome messaging --------------------------------------------------------


def test_rejection_and_outage_never_conflate() -> None:
    rejected = login_failure_message(CredentialsRejectedError("no"))
    unavailable = login_failure_message(CrmUnavailableError("down"))
    assert rejected == SIGN_IN_REJECTED
    assert unavailable == SIGN_IN_CRM_UNAVAILABLE
    assert rejected != unavailable


def test_rejection_message_never_enumerates_accounts() -> None:
    text = " ".join(
        [SIGN_IN_REJECTED.what_happened, SIGN_IN_REJECTED.why, SIGN_IN_REJECTED.what_next]
    ).lower()
    # Neither which field failed nor whether the account exists may leak.
    assert "exist" not in text
    assert "unknown user" not in text
    assert "wrong password" not in text


def test_unmapped_outcomes_fail_toward_not_your_fault() -> None:
    class SurpriseOutcomeError(CrmAuthError):
        pass

    assert login_failure_message(SurpriseOutcomeError()) == SIGN_IN_CRM_UNAVAILABLE
    assert login_failure_message(CrmCredentialExpiredError()) == SIGN_IN_CRM_UNAVAILABLE


def test_reset_confirmation_is_uniform_by_construction() -> None:
    # One constant serves both the account-found and account-unknown outcomes;
    # its wording must stay conditional ("if an account matches").
    assert "if an account matches" in RESET_REQUESTED.why.lower()


# --- Cross-window re-auth & unsaved work ---------------------------------------


def _two_windows() -> tuple[WindowSessionController, WindowSessionController]:
    channel = InMemorySessionChannel()
    return (
        WindowSessionController(channel, reference="ref-old"),
        WindowSessionController(channel, reference="ref-old"),
    )


def test_reauth_prompt_preserves_work_and_holds_requests() -> None:
    window, _ = _two_windows()
    window.unsaved_work["notes"] = "half-typed session notes"
    message = window.on_reauth_required()
    window.hold("PATCH /records/mentee/123")
    assert window.phase is WindowPhase.REAUTH_PROMPT
    assert window.unsaved_work == {"notes": "half-typed session notes"}
    assert "unsaved work is untouched" in message.what_next


def test_one_relogin_restores_all_windows_and_replays_held_requests() -> None:
    first, second = _two_windows()
    first.on_reauth_required()
    second.on_reauth_required()
    second.hold("POST /records/note")
    replayed: list[str] = []
    second.replay = replayed.append  # type: ignore[method-assign]

    held_here = first.complete_reauth("ref-new")

    assert held_here == []
    assert first.phase is WindowPhase.ACTIVE
    assert second.phase is WindowPhase.ACTIVE
    assert first.reference == second.reference == "ref-new"
    assert replayed == ["POST /records/note"]


def test_revival_does_not_resurrect_an_ended_window() -> None:
    first, second = _two_windows()
    first.on_reauth_required()
    second.on_session_ended()
    first.complete_reauth("ref-new")
    assert second.phase is WindowPhase.ENDED
    assert second.reference == "ref-old"


def test_ended_session_keeps_the_work_on_screen() -> None:
    window, _ = _two_windows()
    window.unsaved_work["draft"] = "wrap-up summary"
    message = window.on_session_ended()
    assert window.phase is WindowPhase.ENDED
    assert window.unsaved_work == {"draft": "wrap-up summary"}
    assert message == SESSION_ENDED


def test_logout_runs_the_dirty_guard_then_ends_every_window() -> None:
    first, second = _two_windows()
    first.unsaved_work["draft"] = "unsent message"
    with pytest.raises(UnsavedWorkGuardError):
        first.logout()
    assert first.phase is WindowPhase.ACTIVE

    first.logout(discard_confirmed=True)
    assert first.phase is WindowPhase.ENDED
    assert second.phase is WindowPhase.ENDED
