"""Unit tests for winonlinux.auth_state (spec.md §6.4, add-auth-account-manager change).

Synthesized ClassifiedMsalError instances only -- no asyncio, no MSAL library, no network. Each
test names the §6.4 transition-table row it exercises.
"""

from __future__ import annotations

import pytest

from winonlinux.auth_errors import ClassifiedMsalError, MsalErrorCategory
from winonlinux.auth_state import (
    MAX_CLAIMS_CHALLENGE_RETRIES,
    AccountAuthState,
    AuthState,
)

pytestmark = pytest.mark.unit

HOME_ACCOUNT_ID = "abc123.def456"


def _active_account(**kwargs) -> AccountAuthState:
    """An account already ACTIVE, the precondition for every apply_error() call in the table."""
    account = AccountAuthState(HOME_ACCOUNT_ID, **kwargs)
    account.mark_active()
    assert account.state is AuthState.ACTIVE
    return account


def _classified(category: MsalErrorCategory, **kwargs) -> ClassifiedMsalError:
    return ClassifiedMsalError(category=category, **kwargs)


# -- one test per transition-table row, starting from ACTIVE -------------------------------


def test_invalid_grant_moves_to_reauth_required_and_resets():
    account = _active_account()
    # Seed pending state that INVALID_GRANT must clear, to prove it actually clears rather than
    # happening to start empty.
    account.apply_error(_classified(MsalErrorCategory.CLAIMS_CHALLENGE, claims="stale-claims"))
    assert account.pending_claims == "stale-claims"

    result = account.apply_error(_classified(MsalErrorCategory.INVALID_GRANT))

    assert result is AuthState.REAUTH_REQUIRED
    assert account.state is AuthState.REAUTH_REQUIRED
    assert account.pending_claims is None
    assert account.pending_admin_consent_url is None
    assert account.retry_count == 0


def test_claims_challenge_moves_to_interactive_auth_with_claims_pending():
    account = _active_account()

    result = account.apply_error(
        _classified(MsalErrorCategory.CLAIMS_CHALLENGE, claims='{"access_token":{"acrs":{"essential":true}}}')
    )

    assert result is AuthState.INTERACTIVE_AUTH
    assert account.state is AuthState.INTERACTIVE_AUTH
    assert account.pending_claims == '{"access_token":{"acrs":{"essential":true}}}'
    assert account.retry_count == 1


def test_network_error_moves_to_offline_without_touching_other_fields():
    account = _active_account()
    # Seed fields directly (bypassing any transition method) that NETWORK_ERROR must leave alone,
    # to prove the handler truly does not touch them rather than happening to start empty.
    account._pending_claims = "still-here"
    account._retry_count = 2

    result = account.apply_error(_classified(MsalErrorCategory.NETWORK_ERROR))

    assert result is AuthState.OFFLINE
    assert account.state is AuthState.OFFLINE
    assert account.pending_claims == "still-here"
    assert account.retry_count == 2
    assert account.pending_admin_consent_url is None


def test_device_ca_blocked_moves_to_device_ca_blocked():
    account = _active_account()

    result = account.apply_error(_classified(MsalErrorCategory.DEVICE_CA_BLOCKED))

    assert result is AuthState.DEVICE_CA_BLOCKED
    assert account.state is AuthState.DEVICE_CA_BLOCKED


def test_consent_required_leaves_state_unchanged_and_sets_url():
    account = _active_account()

    result = account.apply_error(
        _classified(MsalErrorCategory.CONSENT_REQUIRED, admin_consent_url="https://example.invalid/consent")
    )

    assert result is AuthState.ACTIVE
    assert account.state is AuthState.ACTIVE
    assert account.pending_admin_consent_url == "https://example.invalid/consent"


def test_unknown_is_treated_like_invalid_grant():
    account = _active_account()
    account.apply_error(_classified(MsalErrorCategory.CLAIMS_CHALLENGE, claims="stale-claims"))

    result = account.apply_error(_classified(MsalErrorCategory.UNKNOWN))

    assert result is AuthState.REAUTH_REQUIRED
    assert account.state is AuthState.REAUTH_REQUIRED
    assert account.pending_claims is None
    assert account.pending_admin_consent_url is None
    assert account.retry_count == 0


# -- CONSENT_REQUIRED truly leaves ANY current state unchanged, not just ACTIVE -----------------


def test_consent_required_leaves_interactive_auth_state_unchanged():
    account = AccountAuthState(HOME_ACCOUNT_ID)
    account.begin_interactive()
    assert account.state is AuthState.INTERACTIVE_AUTH

    result = account.apply_error(
        _classified(MsalErrorCategory.CONSENT_REQUIRED, admin_consent_url="https://example.invalid/consent2")
    )

    assert result is AuthState.INTERACTIVE_AUTH
    assert account.state is AuthState.INTERACTIVE_AUTH
    assert account.pending_admin_consent_url == "https://example.invalid/consent2"


# -- bounded claims-challenge retries (FR-4-AC-4) -------------------------------------------


def test_claims_challenge_bounded_retry_lands_in_reauth_required_on_fourth():
    account = _active_account()
    assert MAX_CLAIMS_CHALLENGE_RETRIES == 3

    # Three consecutive claims challenges stay in the interactive-retry cycle.
    for attempt in range(1, MAX_CLAIMS_CHALLENGE_RETRIES + 1):
        result = account.apply_error(
            _classified(MsalErrorCategory.CLAIMS_CHALLENGE, claims=f"claims-{attempt}")
        )
        assert result is AuthState.INTERACTIVE_AUTH
        assert account.retry_count == attempt
        assert account.pending_claims == f"claims-{attempt}"

    # The fourth consecutive challenge exceeds the bound and forces full re-auth instead of
    # looping forever in INTERACTIVE_AUTH.
    result = account.apply_error(
        _classified(MsalErrorCategory.CLAIMS_CHALLENGE, claims="claims-4-should-not-be-retried")
    )

    assert result is AuthState.REAUTH_REQUIRED
    assert account.state is AuthState.REAUTH_REQUIRED
    assert account.pending_claims is None


def test_claims_pending_carries_exact_string_verbatim():
    account = _active_account()
    tricky_claims = (
        '{"access_token": {"acrs": {"essential": true, "value": "urn:test claim\\n\\twith '
        'whitespace \\"and quotes\\" & special <chars> ünïcödé"}}}  '
    )

    account.apply_error(_classified(MsalErrorCategory.CLAIMS_CHALLENGE, claims=tricky_claims))

    # Exact string, no trimming, no normalization, no escaping change.
    assert account.pending_claims == tricky_claims
    assert account.pending_claims is tricky_claims or account.pending_claims == tricky_claims


# -- DEVICE_CA_BLOCKED terminality -----------------------------------------------------------


def test_device_ca_blocked_is_terminal_only_sign_out_moves_away():
    account = _active_account()
    account.apply_error(_classified(MsalErrorCategory.DEVICE_CA_BLOCKED))
    assert account.state is AuthState.DEVICE_CA_BLOCKED

    # A different category must not move the account away from DEVICE_CA_BLOCKED.
    result = account.apply_error(_classified(MsalErrorCategory.INVALID_GRANT))
    assert result is AuthState.DEVICE_CA_BLOCKED
    assert account.state is AuthState.DEVICE_CA_BLOCKED

    # Nor may CLAIMS_CHALLENGE, NETWORK_ERROR, CONSENT_REQUIRED, or UNKNOWN.
    for category in (
        MsalErrorCategory.CLAIMS_CHALLENGE,
        MsalErrorCategory.NETWORK_ERROR,
        MsalErrorCategory.CONSENT_REQUIRED,
        MsalErrorCategory.UNKNOWN,
    ):
        result = account.apply_error(_classified(category, claims="x", admin_consent_url="https://x.invalid"))
        assert result is AuthState.DEVICE_CA_BLOCKED
        assert account.state is AuthState.DEVICE_CA_BLOCKED

    # Only sign_out() moves it away.
    account.sign_out()
    assert account.state is AuthState.SIGNED_OUT


# -- mark_active() fully resets ------------------------------------------------------------


def test_mark_active_resets_retry_count_and_pending_fields():
    account = _active_account()
    account.apply_error(
        _classified(MsalErrorCategory.CLAIMS_CHALLENGE, claims="pending-claims-before-reauth")
    )
    assert account.retry_count == 1
    assert account.pending_claims == "pending-claims-before-reauth"

    account.apply_error(
        _classified(MsalErrorCategory.CONSENT_REQUIRED, admin_consent_url="https://example.invalid/consent3")
    )
    assert account.pending_admin_consent_url == "https://example.invalid/consent3"

    account.mark_active()

    assert account.state is AuthState.ACTIVE
    assert account.retry_count == 0
    assert account.pending_claims is None
    assert account.pending_admin_consent_url is None


def test_mark_active_updates_login_hint_when_given():
    account = AccountAuthState(HOME_ACCOUNT_ID, login_hint="old@example.invalid")
    account.mark_active(login_hint="new@example.invalid")
    assert account.login_hint == "new@example.invalid"


def test_mark_active_leaves_login_hint_untouched_when_not_given():
    account = AccountAuthState(HOME_ACCOUNT_ID, login_hint="keep@example.invalid")
    account.mark_active()
    assert account.login_hint == "keep@example.invalid"


# -- other basic lifecycle behavior, for completeness ----------------------------------------


def test_new_account_starts_signed_out():
    account = AccountAuthState(HOME_ACCOUNT_ID)
    assert account.state is AuthState.SIGNED_OUT
    assert account.home_account_id == HOME_ACCOUNT_ID
    assert account.login_hint is None
    assert account.pending_claims is None
    assert account.pending_admin_consent_url is None
    assert account.retry_count == 0


def test_begin_interactive_moves_to_interactive_auth():
    account = AccountAuthState(HOME_ACCOUNT_ID)
    account.begin_interactive()
    assert account.state is AuthState.INTERACTIVE_AUTH


def test_begin_interactive_refuses_to_leave_device_ca_blocked():
    # A caller bypassing apply_error() (e.g. AuthManager.reauth_from_banner) must not be able to
    # reopen a terminal, never-retried DEVICE_CA_BLOCKED account by calling begin_interactive()
    # directly -- the same guarantee apply_error() enforces against classified errors.
    account = AccountAuthState(HOME_ACCOUNT_ID)
    account.begin_interactive()
    account.mark_active()
    account.apply_error(ClassifiedMsalError(category=MsalErrorCategory.DEVICE_CA_BLOCKED))
    assert account.state is AuthState.DEVICE_CA_BLOCKED

    with pytest.raises(RuntimeError):
        account.begin_interactive()
    assert account.state is AuthState.DEVICE_CA_BLOCKED

    # sign_out() is still the one documented way out.
    account.sign_out()
    assert account.state is AuthState.SIGNED_OUT
    account.begin_interactive()
    assert account.state is AuthState.INTERACTIVE_AUTH


def test_begin_silent_refresh_marks_state_only():
    account = _active_account()
    account.begin_silent_refresh()
    assert account.state is AuthState.SILENT_REFRESH


def test_mark_offline_direct_path():
    account = _active_account()
    account.mark_offline()
    assert account.state is AuthState.OFFLINE


def test_sign_out_clears_everything():
    account = _active_account(login_hint="user@example.invalid")
    account.apply_error(_classified(MsalErrorCategory.CLAIMS_CHALLENGE, claims="pending"))
    account.apply_error(
        _classified(MsalErrorCategory.CONSENT_REQUIRED, admin_consent_url="https://example.invalid/consent4")
    )

    account.sign_out()

    assert account.state is AuthState.SIGNED_OUT
    assert account.pending_claims is None
    assert account.pending_admin_consent_url is None
    assert account.retry_count == 0
    assert account.login_hint is None
