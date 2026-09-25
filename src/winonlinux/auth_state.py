"""Per-account authentication state machine (spec.md §6.4, add-auth-account-manager change).

Implements tasks.md 3.1/3.4/3.5: an explicit, unit-testable per-account state object driven by
classified MSAL errors (see :mod:`winonlinux.auth_errors`). Each signed-in account owns exactly one
:class:`AccountAuthState`, keyed by its ``home_account_id`` (FR-3), and every state transition goes
through this module rather than being decided ad hoc at call sites -- this is what makes
``ReauthRequired`` isolate to one account (FR-4-AC-3), claims challenges bounded (FR-4-AC-4), and
``DeviceCABlocked`` truly terminal (§6.5) testable with synthesized errors and no live MSAL calls.

Security note (spec.md §10.7): this module never receives or stores tokens, authorization codes, or
client secrets -- only account identifiers, enum states, a raw ``claims`` string (opaque CA/CAE
policy text, not secret material) and an admin-consent URL. Nothing here needs redaction, but the
discipline is kept anyway: transition logging below names the error *category* and resulting state,
never the ``claims`` value itself, so a future edit that adds more logging has a pattern to follow
rather than a blank page.
"""

from __future__ import annotations

import enum
import logging
from typing import Callable

from winonlinux.auth_errors import ClassifiedMsalError, MsalErrorCategory

__all__ = [
    "AuthState",
    "AccountAuthState",
    "MAX_CLAIMS_CHALLENGE_RETRIES",
]

logger = logging.getLogger(__name__)

#: Bound on consecutive claims-challenge retries before giving up and requiring interactive
#: re-auth from scratch (FR-4-AC-4, spec.md §6.4, risk 8: "never issue an unmodified retry of a
#: claims-challenged request" -- and never retry it unboundedly either).
MAX_CLAIMS_CHALLENGE_RETRIES = 3


class AuthState(enum.Enum):
    """The seven per-account authentication states, per spec.md §6.4."""

    SIGNED_OUT = "signed_out"
    INTERACTIVE_AUTH = "interactive_auth"
    ACTIVE = "active"
    SILENT_REFRESH = "silent_refresh"
    REAUTH_REQUIRED = "reauth_required"
    OFFLINE = "offline"
    DEVICE_CA_BLOCKED = "device_ca_blocked"


# A transition handler receives the account whose apply_error() is in progress and the classified
# error, mutates it as needed, and returns the resulting AuthState. Kept as small closures over
# module-level helpers so the dispatch table below stays pure data (design.md: "state machine as
# data, not control flow"), not a chain of if/elif branches.


def _on_invalid_grant(account: "AccountAuthState", _classified: ClassifiedMsalError) -> AuthState:
    account._pending_claims = None
    account._pending_admin_consent_url = None
    account._retry_count = 0
    return AuthState.REAUTH_REQUIRED


def _on_claims_challenge(account: "AccountAuthState", classified: ClassifiedMsalError) -> AuthState:
    account._retry_count += 1
    if account._retry_count > MAX_CLAIMS_CHALLENGE_RETRIES:
        # Bounded retries exceeded (FR-4-AC-4): stop cycling through claims challenges and fall
        # back to requiring a full interactive re-auth instead.
        account._pending_claims = None
        return AuthState.REAUTH_REQUIRED
    # Carry the claims parameter through VERBATIM -- exact string, no modification (risk 8).
    account._pending_claims = classified.claims
    return AuthState.INTERACTIVE_AUTH


def _on_network_error(account: "AccountAuthState", _classified: ClassifiedMsalError) -> AuthState:
    # Does not touch retry_count, pending_claims, or pending_admin_consent_url (FR-4-AC-5): an
    # unexpired cached access token keeps serving requests while offline, and claims/consent
    # progress from before the outage should not be discarded by a transient network blip.
    return AuthState.OFFLINE


def _on_device_ca_blocked(account: "AccountAuthState", _classified: ClassifiedMsalError) -> AuthState:
    # Terminal for this account this session (§6.5): never ReauthRequired, never retried. The
    # guard in apply_error() below additionally ensures no *other* category can move an account
    # already in this state anywhere but sign_out().
    return AuthState.DEVICE_CA_BLOCKED


def _on_consent_required(account: "AccountAuthState", classified: ClassifiedMsalError) -> AuthState:
    # Consent handling gets its own guided flow, not one of the seven §6.4 states -- the current
    # AuthState is returned unchanged; only the admin-consent URL is recorded.
    account._pending_admin_consent_url = classified.admin_consent_url
    return account._state


# UNKNOWN fails safe toward requiring interactive re-auth rather than silently ignoring an
# unrecognized error shape -- treated exactly like INVALID_GRANT, not as its own branch.
_TRANSITIONS: dict[MsalErrorCategory, Callable[["AccountAuthState", ClassifiedMsalError], AuthState]] = {
    MsalErrorCategory.INVALID_GRANT: _on_invalid_grant,
    MsalErrorCategory.CLAIMS_CHALLENGE: _on_claims_challenge,
    MsalErrorCategory.NETWORK_ERROR: _on_network_error,
    MsalErrorCategory.DEVICE_CA_BLOCKED: _on_device_ca_blocked,
    MsalErrorCategory.CONSENT_REQUIRED: _on_consent_required,
    MsalErrorCategory.UNKNOWN: _on_invalid_grant,
}


class AccountAuthState:
    """The auth state machine for one signed-in account, keyed by ``home_account_id`` (FR-3).

    Starts in :attr:`AuthState.SIGNED_OUT`. Callers drive transitions through the methods below;
    nothing outside this class should assign to ``.state`` or the ``pending_*`` fields directly.
    """

    def __init__(self, home_account_id: str, *, login_hint: str | None = None) -> None:
        self._home_account_id = home_account_id
        self._login_hint = login_hint
        self._state = AuthState.SIGNED_OUT
        self._pending_claims: str | None = None
        self._pending_admin_consent_url: str | None = None
        self._retry_count = 0

    # -- read-only view --------------------------------------------------------

    @property
    def home_account_id(self) -> str:
        return self._home_account_id

    @property
    def state(self) -> AuthState:
        return self._state

    @property
    def pending_claims(self) -> str | None:
        return self._pending_claims

    @property
    def pending_admin_consent_url(self) -> str | None:
        return self._pending_admin_consent_url

    @property
    def login_hint(self) -> str | None:
        return self._login_hint

    @property
    def retry_count(self) -> int:
        return self._retry_count

    # -- transitions -------------------------------------------------------------

    def begin_interactive(self) -> None:
        """Any state except DEVICE_CA_BLOCKED -> INTERACTIVE_AUTH (starting/restarting sign-in).

        DEVICE_CA_BLOCKED is terminal for this account this session (spec.md section 6.5: "never
        retried") -- the same guard apply_error() enforces against a classified error moving a
        blocked account anywhere applies here too, since a caller (e.g. a stale ReauthRequired
        banner reference) could otherwise reopen a supposedly terminal account by calling this
        method directly instead of going through apply_error(). Raises RuntimeError rather than
        silently no-op'ing, so a caller bug surfaces immediately instead of quietly doing nothing.
        """
        if self._state is AuthState.DEVICE_CA_BLOCKED:
            raise RuntimeError(
                f"account {self._home_account_id}: cannot begin interactive auth from "
                "DEVICE_CA_BLOCKED -- this state is terminal for the session; sign_out() first"
            )
        self._log_transition("begin_interactive", AuthState.INTERACTIVE_AUTH)
        self._state = AuthState.INTERACTIVE_AUTH

    def mark_active(self, *, login_hint: str | None = None) -> None:
        """Record a successful (re)authentication -> ACTIVE.

        Clears any pending claims challenge or admin-consent URL, resets the claims-challenge
        retry counter, and updates ``login_hint`` when one is given.

        No-ops (with a log line) on a DEVICE_CA_BLOCKED account -- §6.5 makes that state terminal
        against EVERY mutator, not just apply_error()/begin_interactive(); a future caller (e.g.
        an action change catching its own errors) must not be able to silently resurrect a
        blocked account (fix-account-lifecycle, audit F-14).
        """
        if self._blocked_guard("mark_active"):
            return
        self._log_transition("mark_active", AuthState.ACTIVE)
        self._state = AuthState.ACTIVE
        self._pending_claims = None
        self._pending_admin_consent_url = None
        self._retry_count = 0
        if login_hint is not None:
            self._login_hint = login_hint

    def begin_silent_refresh(self) -> None:
        """ACTIVE/OFFLINE -> SILENT_REFRESH. A marker only -- callers still call
        apply_error()/mark_active()/mark_offline() with the outcome once the silent acquisition
        completes. OFFLINE is a legal source state: an offline account's next silent attempt is
        exactly the §6.4 recovery path (fix-account-lifecycle, audit F-19). No-ops on a
        DEVICE_CA_BLOCKED account (§6.5, audit F-14)."""
        if self._blocked_guard("begin_silent_refresh"):
            return
        self._log_transition("begin_silent_refresh", AuthState.SILENT_REFRESH)
        self._state = AuthState.SILENT_REFRESH

    def apply_error(self, classified: ClassifiedMsalError) -> AuthState:
        """Drive the §6.4 transition table from a classified MSAL error.

        Legal source states: ACTIVE, SILENT_REFRESH, INTERACTIVE_AUTH, and OFFLINE -- callers do
        not call this from SIGNED_OUT. OFFLINE is named explicitly (fix-account-lifecycle, audit
        F-19) because it is a real caller's state: an offline account's next silent attempt
        classifies its outcome from OFFLINE (or from SILENT_REFRESH once begin_silent_refresh()
        has run). DEVICE_CA_BLOCKED is additionally enforced here as terminal regardless of
        caller discipline: once an account is DEVICE_CA_BLOCKED, no category moves it anywhere
        except an explicit :meth:`sign_out`.
        """
        if self._state is AuthState.DEVICE_CA_BLOCKED:
            return AuthState.DEVICE_CA_BLOCKED

        handler = _TRANSITIONS.get(classified.category, _on_invalid_grant)
        new_state = handler(self, classified)
        self._log_transition(f"apply_error[{classified.category.value}]", new_state)
        self._state = new_state
        return new_state

    def mark_offline(self) -> None:
        """Explicit network-exception path -> OFFLINE, for callers that catch a network exception
        directly rather than routing it through :meth:`apply_error`. No-ops on a
        DEVICE_CA_BLOCKED account (§6.5, audit F-14)."""
        if self._blocked_guard("mark_offline"):
            return
        self._log_transition("mark_offline", AuthState.OFFLINE)
        self._state = AuthState.OFFLINE

    def sign_out(self) -> None:
        """-> SIGNED_OUT, clearing everything: pending claims/consent, retry count, login hint."""
        self._log_transition("sign_out", AuthState.SIGNED_OUT)
        self._state = AuthState.SIGNED_OUT
        self._pending_claims = None
        self._pending_admin_consent_url = None
        self._retry_count = 0
        self._login_hint = None

    # -- internal -------------------------------------------------------------

    def _blocked_guard(self, trigger: str) -> bool:
        """True (and logs) when the account is DEVICE_CA_BLOCKED and ``trigger`` must no-op.

        No-op-with-log rather than raise: these mutators are called from error/outcome paths,
        where a raise would turn a future caller's bug into a crash; only the deliberate user
        action (:meth:`begin_interactive`) keeps its hard RuntimeError refusal.
        """
        if self._state is not AuthState.DEVICE_CA_BLOCKED:
            return False
        logger.warning(
            "account %s: ignoring %s() -- DEVICE_CA_BLOCKED is terminal for this session; "
            "only sign_out() moves away from it (spec.md §6.5)",
            self._home_account_id,
            trigger,
        )
        return True

    def _log_transition(self, trigger: str, new_state: AuthState) -> None:
        # Account identifiers and state transitions are logged freely; token/claims *values* never
        # are -- only the fact that a claims challenge or consent URL is now pending, never its
        # content (spec.md §10.7).
        logger.debug(
            "account %s: %s -> %s via %s",
            self._home_account_id,
            self._state.value,
            new_state.value,
            trigger,
        )
