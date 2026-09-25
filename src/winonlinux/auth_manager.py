"""AuthManager: the integration piece tying MSAL, the loopback listener, the keyring-backed cache,
and the per-account auth state machine together (add-auth-account-manager change; tasks.md
sections 1.1, 1.3, 1.4, 2.3, 2.4, 4.1, 4.2, 5.1-5.3, 6.1-6.2).

This module owns exactly one ``msal.PublicClientApplication`` per process (design.md: "MSAL
PublicClientApplication per process, one instance, shared token cache") and exposes the operations
the rest of the app drives sign-in and token acquisition through:

- :meth:`AuthManager.start` -- build the keyring-backed cache, rebuild the account list from it.
- :meth:`AuthManager.add_account` -- brand-new interactive sign-in.
- :meth:`AuthManager.reauth_from_banner` -- interactive re-auth for an existing account.
- :meth:`AuthManager.switch_active_account` / :meth:`AuthManager.sign_out` -- multi-account ops.
- :meth:`AuthManager.acquire_token_silently` -- THE single entry point every outbound call goes
  through first (FR-4-AC-1); it never returns a stale/expired token silently, only a fresh access
  token or one of the exceptions defined below.

It imports, but does not redefine, the four sibling modules built in parallel against the same
shared contract: :mod:`winonlinux.auth_errors` (MSAL error classification),
:mod:`winonlinux.auth_state` (the per-account §6.4 state machine), :mod:`winonlinux.auth_loopback`
(the hardened OAuth loopback redirect listener, D-6), and :mod:`winonlinux.auth_cache` (the
keyring-backed token cache and its cross-account write serializer, D-2).

Placeholder client ID (D-19 not yet done)
------------------------------------------
The real Entra app registration does not exist yet -- that is Stage 0 work. Until it does,
:data:`_CLIENT_ID_PLACEHOLDER` is used and :meth:`AuthManager.start` logs a one-time WARNING the
first time it runs with the placeholder still in place, so a placeholder-driven auth failure is
never mistaken for a real bug in this code.

ASSUMED, pending Stage 0 / real-tenant verification (spec.md §12 risk 14, "verify, don't assert" --
the same discipline add-app-foundation's FreeRDP version-probe follows):

- The exact AADSTS device-state code set :mod:`winonlinux.auth_errors` classifies as
  ``DEVICE_CA_BLOCKED`` (that module's own docstring carries the detail; this module only consumes
  the resulting category).
- That a home_account_id can be reliably reconstructed as ``f"{oid}.{tid}"`` from a successful
  interactive sign-in's ``id_token_claims`` (:func:`_extract_home_account_id` below) -- this is
  MSAL's documented internal convention, not something exercised against a real tenant here.
- MSAL's exact error-result dict shapes for ``initiate_auth_code_flow`` /
  ``acquire_token_by_auth_code_flow`` / ``acquire_token_silent_with_error`` (consumed via
  :mod:`winonlinux.auth_errors`, not re-parsed here).

Logging discipline (spec.md §10.7) -- THIS MODULE IS SECURITY-SENSITIVE
--------------------------------------------------------------------------
Every token, authorization code, PKCE verifier, and claims-challenge value that passes through this
module is *never* logged, at any level -- not even DEBUG. Concretely: this module never logs
``result["access_token"]``, ``result["refresh_token"]``, ``result["id_token"]``, a
``ClassifiedMsalError.claims`` value, or an MSAL "flow" dict (which internally carries the PKCE
``code_verifier``). What IS logged freely: account identifiers (``home_account_id``, which is not
secret -- an opaque MSAL-assigned string, not a credential), ``AuthState``/category names, and
coarse operation names. Log lines below name *that* a claims challenge or consent URL is pending,
never its *content* -- mirroring the discipline :mod:`winonlinux.auth_state` already follows.
"""

from __future__ import annotations

import asyncio
import logging
import webbrowser
from typing import Any, Callable, NoReturn

import msal
import requests

from winonlinux import auth_errors
from winonlinux.auth_cache import build_persisted_cache, cache_write_serializer
from winonlinux.auth_errors import ClassifiedMsalError, MsalErrorCategory
from winonlinux.auth_loopback import LoopbackListener, LoopbackTimeout
from winonlinux.auth_state import AccountAuthState, AuthState
from winonlinux.task_registry import TaskRegistry, run_blocking

__all__ = [
    "AuthError",
    "AuthManagerNotStarted",
    "AccountUnknownError",
    "ReauthRequiredError",
    "OfflineNoCachedTokenError",
    "DeviceCaBlockedError",
    "ConsentRequiredError",
    "ClaimsChallengeError",
    "InteractiveSignInFailedError",
    "SignInCancelled",
    "AuthManager",
]

logger = logging.getLogger(__name__)

# D-19 is not done yet (Stage 0 work): no real Entra app registration exists. This placeholder is
# a deliberately invalid, all-zero GUID -- any MSAL call made with it WILL fail, loudly, and that
# failure is expected. See _warn_placeholder_client_id_once() below: AuthManager.start() logs a
# one-time WARNING the first time it runs with this placeholder still in place, specifically so a
# placeholder-driven failure is never mistaken for a real bug in this module.
_CLIENT_ID_PLACEHOLDER = "00000000-0000-0000-0000-000000000000"

_DEFAULT_REDIRECT_SCOPES: tuple[str, ...] = (
    "openid",
    "profile",
    "offline_access",
    "CloudPC.Read.All",
)

_DEFAULT_INTERACTIVE_TIMEOUT_SECONDS = 300.0

_placeholder_warning_emitted = False


def _warn_placeholder_client_id_once() -> None:
    """Log a one-time WARNING that AuthManager is running with the D-19 placeholder client ID.

    Process-wide, not per-instance: multiple AuthManager instances in one process (e.g. across
    tests) still only warn once, matching "the first time AuthManager.start() runs" rather than
    spamming the log for every instance.
    """
    global _placeholder_warning_emitted
    if _placeholder_warning_emitted:
        return
    _placeholder_warning_emitted = True
    logger.warning(
        "AuthManager is starting with the D-19 PLACEHOLDER client ID (%s) -- no real Entra app "
        "registration exists yet (Stage 0 work, not done, spec.md D-19). Any authentication "
        "failure from this point on is EXPECTED and is not a bug in this module; do not spend "
        "time debugging MSAL flows against this build until a real client_id is supplied.",
        _CLIENT_ID_PLACEHOLDER,
    )


# --- Exceptions ------------------------------------------------------------------------------
#
# acquire_token_silently() raises one of these rather than ever returning a stale/expired token
# silently (FR-4-AC-1). add_account()/reauth_from_banner() raise the subset that can occur before
# an AccountAuthState's own transition table has run (InteractiveSignInFailedError) or that mirror
# it exactly (the rest). SignInCancelled is deliberately NOT an AuthError -- see its docstring.


class AuthError(Exception):
    """Base class for every authentication error AuthManager raises."""


class AuthManagerNotStarted(AuthError):
    """A public operation was invoked before :meth:`AuthManager.start` completed -- including
    after a :class:`~winonlinux.auth_cache.KeyringUnavailable` startup failure (D-2's refusal
    state). Raised BEFORE any side effect (no loopback socket is opened, no MSAL call is made),
    so the refusal is enforced at the API boundary rather than surfacing as a raw
    ``AttributeError`` from an unset ``_app`` (fix-account-lifecycle, audit F-13)."""

    def __init__(self) -> None:
        super().__init__(
            "AuthManager.start() has not completed; sign-in and token operations are "
            "unavailable (the keyring may be unavailable -- D-2 has no fallback)"
        )


class AccountUnknownError(AuthError):
    """The requested ``home_account_id`` is not (or is no longer) in :attr:`AuthManager.accounts`
    -- typically a caller holding a stale id after :meth:`AuthManager.sign_out` removed the
    account. Typed so lifecycle-shaped failures never surface as a bare ``KeyError``
    (fix-account-lifecycle, audit F-05/F-13); a poll loop receiving this treats it as terminal
    for the signed-out account rather than retrying forever."""

    def __init__(self, home_account_id: str) -> None:
        super().__init__(f"account {home_account_id} is not signed in (unknown or removed)")
        self.home_account_id = home_account_id


class ReauthRequiredError(AuthError):
    """The account needs a full interactive re-authentication (§6.4 ReauthRequired)."""

    def __init__(self, home_account_id: str | None) -> None:
        super().__init__(f"account {home_account_id} requires interactive re-authentication")
        self.home_account_id = home_account_id


class OfflineNoCachedTokenError(AuthError):
    """A network failure occurred during silent acquisition and no valid cached access token
    remains to serve the caller (FR-4-AC-5's Offline state, exhausted)."""

    def __init__(self, home_account_id: str) -> None:
        super().__init__(f"account {home_account_id} is offline and has no valid cached token")
        self.home_account_id = home_account_id


class DeviceCaBlockedError(AuthError):
    """The account is blocked by tenant device-state Conditional Access (§6.5) -- terminal for
    this account this session; never retried, never becomes ReauthRequired."""

    def __init__(self, home_account_id: str | None) -> None:
        super().__init__(
            f"account {home_account_id} is blocked by the tenant's device-state Conditional "
            "Access policy"
        )
        self.home_account_id = home_account_id


class ConsentRequiredError(AuthError):
    """Tenant admin consent is missing (§9's guided admin-consent flow)."""

    def __init__(self, home_account_id: str | None, admin_consent_url: str | None) -> None:
        super().__init__(f"account {home_account_id} requires tenant admin consent")
        self.home_account_id = home_account_id
        self.admin_consent_url = admin_consent_url


class ClaimsChallengeError(AuthError):
    """The account must complete an interactive claims challenge before this call can succeed
    (FR-4-AC-4) -- distinct from ReauthRequiredError because bounded retries are not yet
    exhausted; the account is in InteractiveAuth, not ReauthRequired.

    ``claims`` carries the exact, unmodified string a caller MUST pass verbatim into the next
    interactive re-auth's ``claims_challenge`` parameter -- never log this value (see module
    docstring's logging discipline).
    """

    def __init__(self, home_account_id: str | None, claims: str | None) -> None:
        super().__init__(f"account {home_account_id} must complete an interactive claims challenge")
        self.home_account_id = home_account_id
        self.claims = claims


class InteractiveSignInFailedError(AuthError):
    """A brand-new interactive sign-in's code exchange failed with a category that has no more
    specific exception available, because no :class:`~winonlinux.auth_state.AccountAuthState`
    exists yet to drive through its transition table (that requires an established
    ``home_account_id``, which a failed brand-new sign-in never produces). Used only by
    :meth:`AuthManager.add_account`'s failure path for INVALID_GRANT/UNKNOWN/NETWORK_ERROR-shaped
    failures -- CONSENT_REQUIRED, DEVICE_CA_BLOCKED, and CLAIMS_CHALLENGE get their own dedicated
    exceptions above even pre-account, since those are meaningful without one.
    """

    def __init__(self, category: MsalErrorCategory, raw_error_description: str | None) -> None:
        super().__init__(f"interactive sign-in failed: {category.value}")
        self.category = category
        self.raw_error_description = raw_error_description


class SignInCancelled(Exception):
    """The user abandoned or cancelled interactive sign-in.

    Deliberately NOT an :class:`AuthError` subclass: spec.md's "User cancels interactive auth"
    scenario requires the application to return to its prior state with **no error dialog** --
    callers must be able to tell "cancelled, show nothing" apart from "real error, tell the user"
    by exception type alone, and inheriting from AuthError would blur that distinction for any
    caller that catches AuthError broadly.
    """


# --- AuthManager -------------------------------------------------------------------------------


class AuthManager:
    """Owns the one process-wide ``msal.PublicClientApplication`` and every account's auth state.

    ``task_registry`` is used for exactly one thing here: cancelling an outgoing account's
    in-flight task group on :meth:`switch_active_account` (D-18, FR-3-AC-2). Every blocking call
    this module makes (keyring I/O, MSAL network calls, spawning the system browser) is dispatched
    through the module-level :func:`winonlinux.task_registry.run_blocking` function directly --
    see the "task_registry API note" below for why, not through a method on ``task_registry``
    itself.
    """

    def __init__(
        self,
        *,
        task_registry: TaskRegistry,
        client_id: str | None = None,
        tenant: str = "organizations",
        redirect_scopes: tuple[str, ...] = _DEFAULT_REDIRECT_SCOPES,
        interactive_timeout_seconds: float = _DEFAULT_INTERACTIVE_TIMEOUT_SECONDS,
    ) -> None:
        self.task_registry = task_registry
        self._tenant = tenant
        self.redirect_scopes: tuple[str, ...] = tuple(redirect_scopes)
        self._interactive_timeout_seconds = interactive_timeout_seconds
        self._client_id = client_id if client_id is not None else _CLIENT_ID_PLACEHOLDER

        self._app: msal.PublicClientApplication | None = None

        # Public: read directly by callers (no property wrapper -- see task description).
        self.accounts: dict[str, AccountAuthState] = {}
        self.active_account_id: str | None = None

        # Per-(account, scopes) single-flight for acquire_token_silently() (tasks.md 4.1,
        # FR-4-AC-6). Keyed on (home_account_id, tuple(sorted(scopes))), not home_account_id
        # alone: two concurrent callers for the same account but DIFFERENT scopes (e.g. one
        # requesting CloudPC.Read.All, another CloudPC.ReadWrite.All after incremental consent)
        # must not collapse into sharing a token scoped for the wrong request.
        self._silent_locks: dict[tuple[str, tuple[str, ...]], asyncio.Lock] = {}
        self._silent_inflight: dict[tuple[str, tuple[str, ...]], "asyncio.Future[str]"] = {}

        # tasks.md 3.1 (add-cloudpc-enumeration) needs a way to react to "the active account is
        # now X" without AuthManager knowing anything about CloudPcProvider or any other consumer
        # -- a plain observer list, notified synchronously from start()/add_account()/
        # switch_active_account() whenever active_account_id is established or changes. A listener
        # is expected to do its own real work (if any) via task_registry.create_task, not block
        # this synchronous notification.
        self._active_account_listeners: list["Callable[[str], None]"] = []

    def add_active_account_listener(self, callback: "Callable[[str], None]") -> None:
        """Register ``callback(home_account_id)`` to be called whenever the active account is
        established or changes: on :meth:`start`'s startup rebuild (if any cached account exists),
        on :meth:`add_account` establishing the very first account, and on every
        :meth:`switch_active_account` call that actually changes the active account. Exceptions a
        callback raises are caught and logged here, never allowed to break the auth flow that
        triggered the notification."""
        self._active_account_listeners.append(callback)

    def _notify_active_account_changed(self, home_account_id: str) -> None:
        for callback in list(self._active_account_listeners):
            try:
                callback(home_account_id)
            except Exception:  # noqa: BLE001 - a listener's own bug must never break auth flow
                logger.exception(
                    "an active-account listener raised while handling account %s",
                    home_account_id,
                )

    # -- startup --------------------------------------------------------------------------------

    async def start(self) -> None:
        """Build the keyring-backed token cache and rebuild the account list from it.

        Raises :class:`winonlinux.auth_cache.KeyringUnavailable` unmodified if no usable OS
        keyring is available -- this method does not swallow it. Entering the D-2 refusal state
        (plain explanation, sign-in disabled) is a UI-level concern outside this change's scope;
        callers (app.py, or the future UI-shell change) decide what to show.
        """
        if self._client_id == _CLIENT_ID_PLACEHOLDER:
            _warn_placeholder_client_id_once()

        # build_persisted_cache can block on a D-Bus round trip -- never call it directly on the
        # loop thread (D-18).
        cache = await run_blocking(build_persisted_cache)

        self._app = msal.PublicClientApplication(
            self._client_id,
            authority=f"https://login.microsoftonline.com/{self._tenant}",
            token_cache=cache,
        )

        # get_accounts() reads the (now-loaded) cache; treated as potentially blocking the same
        # way every other MSAL call in this module is, for consistency.
        cached_accounts = await run_blocking(self._app.get_accounts)

        self.accounts = {}
        self.active_account_id = None
        for account in cached_accounts:
            home_account_id = account["home_account_id"]
            state = AccountAuthState(home_account_id, login_hint=account.get("username"))
            # A cached account with tokens on disk is presumptively usable until proven otherwise
            # by an actual acquire_token_silently() call -- no interactive prompt at startup
            # (spec.md's "Startup rebuild" scenario).
            state.mark_active()
            self.accounts[home_account_id] = state
            # Known simplification (tasks.md 2.3): there is no persisted "which account was active
            # last" concept yet in this change. We deterministically pick the first account MSAL's
            # get_accounts() enumerates as the active one when any cached accounts exist. A later
            # change may persist a real last-active marker instead of relying on enumeration order.
            if self.active_account_id is None:
                self.active_account_id = home_account_id

        logger.info("AuthManager.start: rebuilt %d cached account(s) from the keyring", len(self.accounts))
        if self.active_account_id is not None:
            self._notify_active_account_changed(self.active_account_id)

    # -- lifecycle guards (fix-account-lifecycle, audit F-05/F-13) -------------------------------

    def _require_started(self) -> None:
        """Raise :class:`AuthManagerNotStarted` unless :meth:`start` completed.

        Called FIRST in every public operation, before any side effect -- in particular before
        :meth:`_run_interactive_auth_code_flow` opens its loopback listener socket, so a manager
        in the D-2 keyring-refusal state never binds a socket for a sign-in that cannot proceed.
        """
        if self._app is None:
            raise AuthManagerNotStarted()

    def _account_state_or_raise(self, home_account_id: str) -> AccountAuthState:
        """Typed lookup: :class:`AccountUnknownError` instead of a bare ``KeyError`` for an
        unknown/removed account (audit F-05 -- the orphaned-poll-loop failure shape)."""
        try:
            return self.accounts[home_account_id]
        except KeyError:
            raise AccountUnknownError(home_account_id) from None

    # -- add / switch / sign out ------------------------------------------------------------------

    async def add_account(self) -> str:
        """Full interactive sign-in for a brand-new account (FR-3-AC-1): does not disturb any
        existing entry in :attr:`accounts` or change :attr:`active_account_id` unless this is the
        very first account ever added.

        Raises :class:`AuthManagerNotStarted` (before any socket is opened) if :meth:`start` has
        not completed, :class:`SignInCancelled` if the user cancels, and the category-appropriate
        :class:`AuthError` subclass (:class:`ConsentRequiredError`, :class:`DeviceCaBlockedError`,
        :class:`ClaimsChallengeError`, or :class:`InteractiveSignInFailedError`) if the code
        exchange itself fails.
        """
        self._require_started()
        result, redirect_uri = await self._run_interactive_auth_code_flow()

        if "error" in result:
            classified = auth_errors.classify_msal_error(
                result, tenant=self._tenant, client_id=self._client_id, redirect_uri=redirect_uri
            )
            logger.warning(
                "add_account: interactive sign-in's code exchange failed (category=%s)",
                classified.category.value,
            )
            if classified.category is MsalErrorCategory.CONSENT_REQUIRED:
                raise ConsentRequiredError(None, classified.admin_consent_url)
            if classified.category is MsalErrorCategory.DEVICE_CA_BLOCKED:
                raise DeviceCaBlockedError(None)
            if classified.category is MsalErrorCategory.CLAIMS_CHALLENGE:
                raise ClaimsChallengeError(None, classified.claims)
            raise InteractiveSignInFailedError(classified.category, classified.raw_error_description)

        home_account_id = await self._resolve_new_home_account_id(result)
        login_hint = _preferred_username(result)

        account_state = AccountAuthState(home_account_id, login_hint=login_hint)
        account_state.mark_active(login_hint=login_hint)
        self.accounts[home_account_id] = account_state

        # Adding a second (or later) account must NOT change which account is active -- only set
        # active_account_id when this is literally the first account ever added.
        if len(self.accounts) == 1:
            self.active_account_id = home_account_id
            self._notify_active_account_changed(home_account_id)

        logger.info("add_account: signed in new account %s", home_account_id)
        return home_account_id

    async def switch_active_account(self, home_account_id: str) -> None:
        """Make ``home_account_id`` the active account, cancelling the outgoing account's
        in-flight work (D-18, FR-3-AC-2). No-op if it is already active. Never touches any
        account's cached tokens or :class:`AccountAuthState` -- purely about which account
        outbound calls are scoped to and cancelling the previous one's in-flight work.
        """
        self._require_started()
        if home_account_id == self.active_account_id:
            return

        previous_account_id = self.active_account_id
        if previous_account_id is not None:
            self.task_registry.cancel_group(previous_account_id)

        self.active_account_id = home_account_id
        logger.info(
            "switch_active_account: %s -> %s", previous_account_id, home_account_id
        )
        # Cancel-then-notify, in that order: a listener reacting to this (e.g. CloudPcProvider
        # starting the new account's enumeration) must run after the old account's task group has
        # already been cancelled above, not before -- otherwise a listener that schedules work
        # under task_registry could race the cancellation.
        self._notify_active_account_changed(home_account_id)

    async def sign_out(self, home_account_id: str) -> None:
        """Remove ``home_account_id``'s cache entries and local state (FR-3-AC-3), then destroy
        its task group so no background work (e.g. the CloudPC poll loop) keeps running -- or
        keeps holding data -- for a signed-out account (fix-account-lifecycle, audit F-05; D-18).
        Every other account's tokens and :class:`AccountAuthState` are untouched.

        The group is destroyed LAST, after every await in this method: a caller running inside
        the account's own task group would otherwise cancel itself mid-removal. (Called from
        inside the group, this method still completes -- the self-cancellation is only delivered
        at the caller's next await, which is after this method returns -- but an app-layer
        sign-out entry point should schedule it from outside the group, the same way
        ``switch_active_account``'s cancel is driven.)"""
        self._require_started()
        msal_account = await self._find_msal_account(home_account_id)
        if msal_account is not None:
            async with cache_write_serializer:
                await run_blocking(self._app.remove_account, msal_account)
        else:
            logger.warning(
                "sign_out: account %s had no matching MSAL account object in the cache; "
                "removing local state only",
                home_account_id,
            )

        self.accounts.pop(home_account_id, None)
        # Keys are (home_account_id, scopes) tuples (see acquire_token_silently) -- drop every
        # entry for this account regardless of which scopes it was keyed under. This does not
        # cancel or otherwise disturb a call already in flight for this account (its `finally`
        # clause pops safely either way, see acquire_token_silently) -- it only stops a NEW call
        # for this now-signed-out account from being deduplicated against stale bookkeeping.
        for key in [k for k in self._silent_locks if k[0] == home_account_id]:
            self._silent_locks.pop(key, None)
        for key in [k for k in self._silent_inflight if k[0] == home_account_id]:
            self._silent_inflight.pop(key, None)

        if self.active_account_id == home_account_id:
            self.active_account_id = next(iter(self.accounts), None)

        # Cancel and drop everything the signed-out account still had in flight -- the poll loop
        # included (FR-3-AC-3; audit F-05's orphaned-poll-loop fix). Deliberately after all the
        # awaits above, see docstring.
        self.task_registry.destroy_group(home_account_id)

        logger.info("sign_out: removed account %s", home_account_id)

    # -- silent acquisition: the single entry point ------------------------------------------------

    async def acquire_token_silently(self, home_account_id: str, scopes: list[str]) -> str:
        """THE single entry point every outbound Graph/feed/launch call goes through first
        (FR-4-AC-1). Never returns a stale/expired token silently -- raises one of this module's
        :class:`AuthError` subclasses instead.

        Concurrent callers requesting the SAME account's token collapse into exactly one
        underlying MSAL call (FR-4-AC-6, tasks.md 4.1): the first caller does the real work behind
        a per-account lock and publishes its result on a shared, per-account
        :class:`asyncio.Future`; every other concurrent caller awaits that same future rather than
        issuing a second call. This mirrors :func:`winonlinux.freerdp_probe.probe`'s own in-flight
        de-duplication pattern, adapted to be per-account instead of global. There is no
        "still-cached-and-valid, skip the call" short-circuit the way ``freerdp_probe`` has:
        ``msal.PublicClientApplication.acquire_token_silent_with_error`` already returns a cached,
        still-valid access token with zero network call on its own when one exists -- this method
        only deduplicates genuinely CONCURRENT calls into that same underlying MSAL call, it does
        not reimplement MSAL's own caching.
        """
        self._require_started()
        dedup_key = (home_account_id, tuple(sorted(scopes)))

        # Fast path: a concurrent call for this same (account, scopes) is already in flight --
        # await its shared future instead of starting a second underlying MSAL call. Checked
        # before acquiring the lock (mirroring freerdp_probe.probe()'s double-checked pattern) so
        # that a caller arriving while another is doing the real work never needlessly contends
        # the lock.
        existing_future = self._silent_inflight.get(dedup_key)
        if existing_future is not None:
            return await existing_future

        lock = self._silent_locks.setdefault(dedup_key, asyncio.Lock())
        async with lock:
            # Re-check: a concurrent caller may have registered (and possibly already completed)
            # an in-flight future while we were waiting for the lock.
            existing_future = self._silent_inflight.get(dedup_key)
            if existing_future is not None:
                return await existing_future

            loop = asyncio.get_running_loop()
            future: "asyncio.Future[str]" = loop.create_future()
            self._silent_inflight[dedup_key] = future
            try:
                token = await self._acquire_token_silently_uncached(home_account_id, scopes)
            except BaseException as exc:  # noqa: BLE001 - deliberately broad: propagate everything
                future.set_exception(exc)
                future.exception()  # mark retrieved so asyncio never warns if no one else awaits it
                raise
            else:
                future.set_result(token)
                return token
            finally:
                # .pop(..., None), not `del`: sign_out() for this same account can concurrently
                # clear _silent_inflight (it has no way to know a call is in flight, by design --
                # signing out must not block on or wait for someone else's pending token request),
                # and a plain `del` would raise a bare, undocumented KeyError here that overrides
                # whatever this call actually produced (a token or a proper AuthError subclass).
                self._silent_inflight.pop(dedup_key, None)

    async def _acquire_token_silently_uncached(self, home_account_id: str, scopes: list[str]) -> str:
        """The actual, non-deduplicated silent acquisition. Only ever called from inside
        :meth:`acquire_token_silently`'s per-account lock -- see that method's docstring."""
        account_state = self._account_state_or_raise(home_account_id)

        # DEVICE_CA_BLOCKED is terminal for this account this session and "never retried"
        # (spec.md section 6.5) -- short-circuit before making any MSAL/network call at all,
        # rather than calling MSAL and letting apply_error() keep the state pinned afterward.
        # Besides being wasted work, repeated failed acquisitions against a CA-blocked account
        # are exactly the kind of noise that shows up in the tenant's own CA telemetry.
        if account_state.state is AuthState.DEVICE_CA_BLOCKED:
            raise DeviceCaBlockedError(home_account_id)

        # Make the §6.4 SilentRefresh state real (fix-account-lifecycle, audit F-19): a silent
        # acquisition for an ACTIVE or OFFLINE account is observable as SILENT_REFRESH for its
        # duration; mark_active()/apply_error()/mark_offline() below record the outcome. Other
        # source states (REAUTH_REQUIRED, INTERACTIVE_AUTH) are left as-is -- a refresh attempted
        # from those is already exceptional and its state should stay honest about that.
        if account_state.state in (AuthState.ACTIVE, AuthState.OFFLINE):
            account_state.begin_silent_refresh()

        msal_account = await self._find_msal_account(home_account_id)
        if msal_account is None:
            logger.warning(
                "account %s: no matching MSAL account object found in the cache; treating as "
                "requiring interactive re-authentication",
                home_account_id,
            )
            account_state.apply_error(ClassifiedMsalError(category=MsalErrorCategory.UNKNOWN))
            raise ReauthRequiredError(home_account_id)

        try:
            async with cache_write_serializer:
                result = await run_blocking(
                    self._app.acquire_token_silent_with_error, scopes, account=msal_account
                )
        except (requests.exceptions.RequestException, OSError, TimeoutError) as exc:
            logger.warning(
                "account %s: silent acquisition failed with a network error (%s)",
                home_account_id,
                type(exc).__name__,
            )
            account_state.mark_offline()
            # NOTE: msal's own acquire_token_silent* already returns a cached, still-unexpired
            # access token with zero network call whenever one exists -- so reaching this except
            # branch at all typically means MSAL itself judged the network necessary (e.g. the
            # cached AT had genuinely expired), in which case there is usually nothing valid left
            # to serve here. This module does not reach into MSAL's internal cache to look for a
            # stale-but-still-valid AT independently of what MSAL itself just tried and failed to
            # do -- per FR-4-AC-1, the only supported path back to a token is a successful
            # acquisition, so this raises rather than fabricate a "probably fine" token.
            raise OfflineNoCachedTokenError(home_account_id) from exc

        if "error" not in result:
            account_state.mark_active()
            return result["access_token"]

        # Deliberately NOT passing tenant/client_id/redirect_uri here: there is no live
        # redirect_uri in a silent (non-interactive) call, so admin_consent_url is left None by
        # classify_msal_error and a caller that needs one triggers interactive re-auth instead,
        # which does have a redirect_uri (see add_account/reauth_from_banner).
        classified = auth_errors.classify_msal_error(result)
        new_state = account_state.apply_error(classified)
        logger.warning(
            "account %s: silent acquisition failed (category=%s, new_state=%s)",
            home_account_id,
            classified.category.value,
            new_state.value,
        )
        _raise_for_state(home_account_id, new_state, classified)

    # -- interactive re-auth from an existing account's banner --------------------------------------

    async def reauth_from_banner(self, home_account_id: str) -> None:
        """Interactive re-auth for an account already in ReauthRequired (or InteractiveAuth from
        a claims challenge). Reuses the same loopback+browser round trip as :meth:`add_account`
        via :meth:`_run_interactive_auth_code_flow`. On success, updates the EXISTING
        :class:`AccountAuthState` in place (never creates a new entry)."""
        self._require_started()
        account_state = self._account_state_or_raise(home_account_id)

        # DEVICE_CA_BLOCKED is terminal for this account this session (spec.md section 6.5) --
        # refuse explicitly, with AuthManager's own documented AuthError, rather than letting
        # AccountAuthState.begin_interactive()'s own guard raise a bare RuntimeError instead. A
        # caller reaching this method for a blocked account is itself a bug (spec.md's own
        # scenario says a blocked account's UI should never offer a ReauthRequired banner in the
        # first place) but this method must still fail safely and clearly if it happens anyway.
        if account_state.state is AuthState.DEVICE_CA_BLOCKED:
            raise DeviceCaBlockedError(home_account_id)

        # apply_error() is only valid from ACTIVE/SILENT_REFRESH/INTERACTIVE_AUTH (see
        # auth_state.py) -- this account is normally in ReauthRequired or InteractiveAuth when
        # this method is called, so begin_interactive() (any state -> InteractiveAuth) puts it
        # into a state a subsequent apply_error() call on failure is valid from.
        previous_state = account_state.state
        account_state.begin_interactive()

        try:
            result, redirect_uri = await self._run_interactive_auth_code_flow(
                login_hint=account_state.login_hint,
                claims_challenge=account_state.pending_claims,
            )
        except SignInCancelled:
            # spec.md "User cancels interactive auth": return to the prior state, no error dialog.
            # AccountAuthState's public API has no "revert to an arbitrary prior state" transition
            # by design (see auth_state.py) -- reaching into the private attribute here is a known
            # layering wart, flagged for promotion to a public method later, rather than leaving
            # the account stuck in InteractiveAuth after a cancelled re-auth attempt.
            account_state._state = previous_state  # noqa: SLF001 - documented workaround above
            logger.info(
                "reauth_from_banner: account %s cancelled interactive re-auth, restored to %s",
                home_account_id,
                previous_state.value,
            )
            raise

        if "error" in result:
            classified = auth_errors.classify_msal_error(
                result, tenant=self._tenant, client_id=self._client_id, redirect_uri=redirect_uri
            )
            new_state = account_state.apply_error(classified)
            logger.warning(
                "reauth_from_banner: account %s interactive re-auth failed "
                "(category=%s, new_state=%s)",
                home_account_id,
                classified.category.value,
                new_state.value,
            )
            _raise_for_state(home_account_id, new_state, classified)
            return  # unreachable -- _raise_for_state always raises; kept for readability

        login_hint = _preferred_username(result)
        account_state.mark_active(login_hint=login_hint)
        logger.info("reauth_from_banner: account %s re-authenticated", home_account_id)

    # -- shared interactive-flow helper -------------------------------------------------------------

    async def _run_interactive_auth_code_flow(
        self, *, login_hint: str | None = None, claims_challenge: str | None = None
    ) -> tuple[dict[str, Any], str]:
        """One full interactive auth-code-with-PKCE round trip: loopback listener + system browser
        + code exchange. Shared by :meth:`add_account` and :meth:`reauth_from_banner` (tasks.md
        1.3/1.4/5.1) rather than duplicated between them.

        Returns ``(token_result, redirect_uri)``. ``token_result`` is MSAL's raw result dict from
        ``acquire_token_by_auth_code_flow`` -- it may itself carry an ``"error"`` key (e.g. consent
        still missing); classifying that is each caller's own job, not this helper's.
        ``redirect_uri`` is returned alongside so a caller that needs to classify a
        consent-required error can compose an admin-consent URL against the same redirect_uri MSAL
        actually used for this attempt.

        Raises :class:`SignInCancelled` -- never lets a cancellation surface as an ordinary
        :class:`AuthError` -- when the user abandons/denies sign-in in the browser: either the
        loopback listener times out (:class:`~winonlinux.auth_loopback.LoopbackTimeout`), or the
        redirect itself carries an ``"error"`` query parameter (spec.md's "user cancels" scenario:
        Entra reports cancellation as an error-shaped redirect, not a raised exception).
        """
        listener = LoopbackListener(timeout_seconds=self._interactive_timeout_seconds)
        port = await listener.start()
        redirect_uri = f"http://127.0.0.1:{port}"

        try:
            flow_kwargs: dict[str, Any] = {}
            if login_hint is not None:
                flow_kwargs["login_hint"] = login_hint
            if claims_challenge is not None:
                flow_kwargs["claims_challenge"] = claims_challenge

            flow = await run_blocking(
                self._app.initiate_auth_code_flow,
                list(self.redirect_scopes),
                redirect_uri=redirect_uri,
                **flow_kwargs,
            )

            # Arm the listener with the expected state BEFORE opening the browser -- the server
            # has been accepting connections since listener.start() above, and an unusually fast
            # redirect (Entra seamless SSO / PRT silent auth can complete in well under a second)
            # could otherwise arrive during this method's own await-suspension points and be
            # rejected as "non-matching" because the listener had no expected state to compare
            # against yet. See LoopbackListener.arm()'s docstring for the full timing argument.
            await listener.arm(flow["state"])

            # Spawning the browser process can briefly block; dispatch off the loop thread too.
            await run_blocking(webbrowser.open, flow["auth_uri"])

            try:
                redirect_result = await listener.wait_for_redirect(flow["state"])
            except LoopbackTimeout as exc:
                logger.info(
                    "interactive sign-in timed out waiting for the browser redirect; treating "
                    "as a cancellation, no error dialog"
                )
                raise SignInCancelled(
                    "loopback listener timed out waiting for the redirect"
                ) from exc
        finally:
            await listener.close()

        if "error" in redirect_result.query_params:
            logger.info(
                "interactive sign-in redirect reported %r; treating as user cancellation, no "
                "error dialog",
                redirect_result.query_params.get("error"),
            )
            raise SignInCancelled(redirect_result.query_params.get("error", "unknown"))

        async with cache_write_serializer:
            token_result = await run_blocking(
                self._app.acquire_token_by_auth_code_flow, flow, redirect_result.query_params
            )

        return token_result, redirect_uri

    # -- small internal helpers ---------------------------------------------------------------------

    async def _find_msal_account(self, home_account_id: str) -> Any | None:
        accounts = await run_blocking(self._app.get_accounts)
        for account in accounts:
            if account.get("home_account_id") == home_account_id:
                return account
        return None

    async def _resolve_new_home_account_id(self, result: dict[str, Any]) -> str:
        """Determine the new account's home_account_id after a successful interactive sign-in.

        Primary path: derive it directly from ``id_token_claims`` as ``f"{oid}.{tid}"`` -- MSAL's
        documented internal convention for home_account_id (ASSUMED, see module docstring).
        Fallback (defensive only -- should not be needed given the "openid" scope is always
        requested): query MSAL's own :meth:`get_accounts` afterward and match by username against
        accounts not already tracked in :attr:`accounts`, since that is the more authoritative
        source of truth when available.
        """
        home_account_id = _extract_home_account_id(result)
        if home_account_id is not None:
            return home_account_id

        username = _preferred_username(result)
        accounts = await run_blocking(self._app.get_accounts)
        for account in accounts:
            candidate = account.get("home_account_id")
            if candidate not in self.accounts and account.get("username") == username:
                return candidate

        raise InteractiveSignInFailedError(
            MsalErrorCategory.UNKNOWN,
            "could not resolve a home_account_id after an apparently successful sign-in",
        )


# --- module-level helpers ------------------------------------------------------------------------


def _extract_home_account_id(result: dict[str, Any]) -> str | None:
    """ASSUMED (see module docstring): MSAL's documented ``f"{oid}.{tid}"`` home_account_id
    convention, derived from a successful token result's ``id_token_claims``."""
    claims = result.get("id_token_claims") or {}
    oid = claims.get("oid")
    tid = claims.get("tid")
    if oid and tid:
        return f"{oid}.{tid}"
    return None


def _preferred_username(result: dict[str, Any]) -> str | None:
    claims = result.get("id_token_claims") or {}
    return claims.get("preferred_username")


def _raise_for_state(
    home_account_id: str, new_state: AuthState, classified: ClassifiedMsalError
) -> NoReturn:
    """Map an :class:`AccountAuthState.apply_error` outcome to the matching exception.

    Shared by :meth:`AuthManager._acquire_token_silently_uncached` and
    :meth:`AuthManager.reauth_from_banner`'s failure paths. CONSENT_REQUIRED is checked first
    because it does not change ``new_state`` at all (auth_state.py's contract: consent handling
    gets its own guided flow, not one of the seven §6.4 states) -- so it can only be recognized via
    ``classified.category``, never via ``new_state`` alone.
    """
    if classified.category is MsalErrorCategory.CONSENT_REQUIRED:
        raise ConsentRequiredError(home_account_id, classified.admin_consent_url)
    if new_state is AuthState.DEVICE_CA_BLOCKED:
        raise DeviceCaBlockedError(home_account_id)
    if new_state is AuthState.REAUTH_REQUIRED:
        raise ReauthRequiredError(home_account_id)
    if new_state is AuthState.INTERACTIVE_AUTH:
        # Reached only via a CLAIMS_CHALLENGE whose bounded retry count is not yet exhausted
        # (apply_error's own transition table) -- interactive re-auth is needed, but the account
        # is not (yet) ReauthRequired.
        raise ClaimsChallengeError(home_account_id, classified.claims)
    # Fail safe: no other combination should occur given auth_state.py's transition table (a
    # NETWORK_ERROR/OFFLINE result never reaches this function -- see
    # _acquire_token_silently_uncached, which handles network exceptions directly via
    # mark_offline() instead of apply_error()). Still fail toward requiring interactive re-auth
    # rather than silently returning nothing.
    raise ReauthRequiredError(home_account_id)
