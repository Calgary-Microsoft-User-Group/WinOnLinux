"""Unit tests for winonlinux.auth_manager (add-auth-account-manager change).

Pure asyncio -- driven with plain ``asyncio.run``/``asyncio.gather``, matching
tests/test_freerdp_probe.py and tests/test_auth_loopback.py's convention rather than a
pytest-asyncio plugin. ``msal.PublicClientApplication`` is never constructed for real: every test
either calls ``AuthManager.start()`` with ``msal.PublicClientApplication`` and
``winonlinux.auth_cache.build_persisted_cache`` monkeypatched, or (for everything below that does
not specifically exercise ``start()``) skips ``start()`` entirely and assigns a stub directly onto
``AuthManager._app``, per the task's own guidance. ``winonlinux.auth_loopback.LoopbackListener`` is
monkeypatched wherever an interactive flow is exercised, so no real TCP listener or browser process
is ever involved.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from winonlinux import auth_manager
from winonlinux.auth_loopback import LoopbackResult, LoopbackTimeout
from winonlinux.auth_manager import (
    AuthManager,
    ClaimsChallengeError,
    DeviceCaBlockedError,
    OfflineNoCachedTokenError,
    ReauthRequiredError,
    SignInCancelled,
)
from winonlinux.auth_state import AccountAuthState, AuthState

pytestmark = pytest.mark.unit

FAKE_ACCESS_TOKEN = "fake-access-token-super-secret-value-should-never-be-logged"
FAKE_CLAIMS = '{"access_token":{"acrs":{"essential":true,"values":["c1"]}}}'


# --- test doubles --------------------------------------------------------------------------------


class FakeTaskRegistry:
    """Minimal stand-in for winonlinux.task_registry.TaskRegistry -- AuthManager calls
    cancel_group() (switch_active_account) and destroy_group() (sign_out, fix-account-lifecycle)
    on it, matching the real class's confirmed signatures."""

    def __init__(self) -> None:
        self.cancel_group_calls: list[object] = []
        self.destroy_group_calls: list[object] = []

    def cancel_group(self, account_id: object) -> None:
        self.cancel_group_calls.append(account_id)

    def destroy_group(self, account_id: object) -> None:
        self.destroy_group_calls.append(account_id)


class FakeMsalApp:
    """Stand-in for msal.PublicClientApplication. Every method AuthManager calls on `self._app`
    is implemented here as a plain synchronous function (AuthManager always dispatches these
    through run_blocking, which runs them in a real thread-pool executor)."""

    def __init__(self) -> None:
        self.accounts: dict[str, dict] = {}
        self.removed_accounts: list[dict] = []

        self.silent_calls = 0
        self.silent_call_args: list[tuple] = []
        self.silent_sleep_seconds = 0.0
        self.silent_result_factory = None  # Callable[[list, dict | None], dict]
        self.silent_exception_factory = None  # Callable[[], BaseException] | None

        self.initiate_flow_calls: list[dict] = []
        self.initiate_flow_result: dict | None = None
        self.acquire_by_code_flow_result: dict | None = None

    # -- account enumeration -----------------------------------------------------------------

    def get_accounts(self):
        return list(self.accounts.values())

    def remove_account(self, account):
        self.removed_accounts.append(account)
        self.accounts.pop(account["home_account_id"], None)

    # -- silent acquisition -------------------------------------------------------------------

    def acquire_token_silent_with_error(self, scopes, account=None):
        self.silent_calls += 1
        self.silent_call_args.append((tuple(scopes), account))
        if self.silent_sleep_seconds:
            time.sleep(self.silent_sleep_seconds)
        if self.silent_exception_factory is not None:
            raise self.silent_exception_factory()
        return self.silent_result_factory(scopes, account)

    # -- interactive flow ---------------------------------------------------------------------

    def initiate_auth_code_flow(self, scopes, redirect_uri=None, **kwargs):
        call = {"scopes": list(scopes), "redirect_uri": redirect_uri, **kwargs}
        self.initiate_flow_calls.append(call)
        return self.initiate_flow_result or {
            "auth_uri": "https://login.microsoftonline.com/fake/authorize",
            "state": "the-expected-state",
        }

    def acquire_token_by_auth_code_flow(self, flow, query_params):
        return self.acquire_by_code_flow_result


class FakeLoopbackListener:
    """Stand-in for winonlinux.auth_loopback.LoopbackListener. `outcome` is read at class scope
    (set by the test right before calling into AuthManager) since these tests only ever have one
    interactive flow in flight at a time."""

    outcome: object = None  # LoopbackResult, or an Exception instance/class to raise

    def __init__(self, *, timeout_seconds: float = 300.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.closed = False

    async def start(self) -> int:
        return 65432

    async def arm(self, expected_state: str) -> None:
        # No-op here: this fake resolves wait_for_redirect() from `outcome` directly rather than
        # actually matching against an armed state, so there is nothing for arm() to do -- it
        # exists on this fake purely so AuthManager's real call to listener.arm(...) (see
        # auth_manager.py's _run_interactive_auth_code_flow) has something to call.
        pass

    async def wait_for_redirect(self, expected_state: str) -> LoopbackResult:
        outcome = FakeLoopbackListener.outcome
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, LoopbackResult)
        return outcome

    async def close(self) -> None:
        self.closed = True


def _make_manager(app: FakeMsalApp | None = None) -> tuple[AuthManager, FakeTaskRegistry]:
    task_registry = FakeTaskRegistry()
    manager = AuthManager(task_registry=task_registry, client_id="test-client-id")
    manager._app = app if app is not None else FakeMsalApp()
    return manager, task_registry


def _seeded_account(manager: AuthManager, home_account_id: str, *, login_hint: str | None = None) -> AccountAuthState:
    # Wired through the manager's own notifier (as _new_account_state does in production) so the
    # add_auth_state_listener tests observe seeded accounts' transitions too.
    state = AccountAuthState(
        home_account_id, login_hint=login_hint, on_transition=manager._notify_auth_state_changed
    )
    state.mark_active(login_hint=login_hint)
    manager.accounts[home_account_id] = state
    return state


# --- 7.2: N-simultaneous-acquisitions (FR-4-AC-6, tasks.md 4.1) ----------------------------------


def test_concurrent_acquire_token_silently_calls_collapse_into_one_underlying_call():
    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-concurrent"
        app.accounts[home_account_id] = {"home_account_id": home_account_id, "username": "a@contoso.com"}
        app.silent_sleep_seconds = 0.05  # slow enough that concurrent callers genuinely overlap
        app.silent_result_factory = lambda scopes, account: {"access_token": FAKE_ACCESS_TOKEN}

        manager, _ = _make_manager(app)
        _seeded_account(manager, home_account_id)

        results = await asyncio.gather(
            *[manager.acquire_token_silently(home_account_id, ["Scope.Read"]) for _ in range(6)]
        )

        assert results == [FAKE_ACCESS_TOKEN] * 6
        assert app.silent_calls == 1

    asyncio.run(scenario())


def test_concurrent_acquire_token_silently_calls_for_different_scopes_are_not_conflated():
    # Two concurrent callers for the SAME account but DIFFERENT scopes must each get their own
    # MSAL call and their own correctly-scoped token -- collapsing them together would hand one
    # caller a token acquired for the other's scopes.
    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-scoped"
        app.accounts[home_account_id] = {"home_account_id": home_account_id, "username": "a@contoso.com"}
        app.silent_sleep_seconds = 0.05

        def _result(scopes, account):
            return {"access_token": "token-for-" + ",".join(scopes)}

        app.silent_result_factory = _result

        manager, _ = _make_manager(app)
        _seeded_account(manager, home_account_id)

        read_token, write_token = await asyncio.gather(
            manager.acquire_token_silently(home_account_id, ["Scope.Read"]),
            manager.acquire_token_silently(home_account_id, ["Scope.ReadWrite"]),
        )

        assert read_token == "token-for-Scope.Read"
        assert write_token == "token-for-Scope.ReadWrite"
        assert app.silent_calls == 2

    asyncio.run(scenario())


def test_sign_out_concurrent_with_in_flight_acquire_does_not_raise_keyerror():
    # Regression test: sign_out() clearing _silent_inflight/_silent_locks while a concurrent
    # acquire_token_silently() for the same account is still in flight must not raise a bare
    # KeyError out of that call's `finally` clause -- the caller should see its real outcome (a
    # token here) rather than an unrelated crash.
    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-signout-race"
        app.accounts[home_account_id] = {"home_account_id": home_account_id, "username": "a@contoso.com"}
        app.silent_sleep_seconds = 0.05
        app.silent_result_factory = lambda scopes, account: {"access_token": FAKE_ACCESS_TOKEN}

        manager, _ = _make_manager(app)
        _seeded_account(manager, home_account_id)

        async def sign_out_shortly_after():
            await asyncio.sleep(0.01)  # let acquire_token_silently register its in-flight future
            await manager.sign_out(home_account_id)

        token, _ = await asyncio.gather(
            manager.acquire_token_silently(home_account_id, ["Scope.Read"]),
            sign_out_shortly_after(),
        )

        assert token == FAKE_ACCESS_TOKEN
        assert home_account_id not in manager.accounts

    asyncio.run(scenario())


def test_sequential_acquire_token_silently_calls_are_not_deduplicated():
    """Two calls that do NOT overlap must each cause their own underlying MSAL call -- the
    single-flight dedup is for genuinely concurrent callers only, not a cache."""

    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-sequential"
        app.accounts[home_account_id] = {"home_account_id": home_account_id, "username": "a@contoso.com"}
        app.silent_result_factory = lambda scopes, account: {"access_token": FAKE_ACCESS_TOKEN}

        manager, _ = _make_manager(app)
        _seeded_account(manager, home_account_id)

        await manager.acquire_token_silently(home_account_id, ["Scope.Read"])
        await manager.acquire_token_silently(home_account_id, ["Scope.Read"])

        assert app.silent_calls == 2

    asyncio.run(scenario())


# --- 5.1: add_account does not disturb an existing account ---------------------------------------


def test_add_account_does_not_disturb_existing_account_or_active_id(monkeypatch):
    async def scenario():
        app = FakeMsalApp()
        manager, _ = _make_manager(app)

        existing_id = "existing-oid.existing-tid"
        _seeded_account(manager, existing_id, login_hint="existing@contoso.com")
        manager.active_account_id = existing_id

        app.acquire_by_code_flow_result = {
            "access_token": FAKE_ACCESS_TOKEN,
            "id_token_claims": {
                "oid": "new-oid",
                "tid": "new-tid",
                "preferred_username": "new@contoso.com",
            },
        }
        FakeLoopbackListener.outcome = LoopbackResult(
            query_params={"code": "auth-code", "state": "the-expected-state"}
        )
        monkeypatch.setattr(auth_manager, "LoopbackListener", FakeLoopbackListener)
        monkeypatch.setattr(auth_manager.webbrowser, "open", lambda *a, **k: True)

        new_id = await manager.add_account()

        assert new_id == "new-oid.new-tid"
        assert new_id in manager.accounts
        assert manager.accounts[new_id].state is AuthState.ACTIVE

        # The pre-existing account is completely untouched.
        assert existing_id in manager.accounts
        assert manager.accounts[existing_id].state is AuthState.ACTIVE
        assert manager.accounts[existing_id].login_hint == "existing@contoso.com"

        # Adding a second account must NOT change which account is active (spec.md scenario).
        assert manager.active_account_id == existing_id

    asyncio.run(scenario())


def test_add_account_sets_active_id_only_when_it_is_the_first_account(monkeypatch):
    async def scenario():
        app = FakeMsalApp()
        manager, _ = _make_manager(app)
        assert manager.active_account_id is None

        app.acquire_by_code_flow_result = {
            "access_token": FAKE_ACCESS_TOKEN,
            "id_token_claims": {"oid": "oid-1", "tid": "tid-1", "preferred_username": "first@contoso.com"},
        }
        FakeLoopbackListener.outcome = LoopbackResult(
            query_params={"code": "auth-code", "state": "the-expected-state"}
        )
        monkeypatch.setattr(auth_manager, "LoopbackListener", FakeLoopbackListener)
        monkeypatch.setattr(auth_manager.webbrowser, "open", lambda *a, **k: True)

        new_id = await manager.add_account()

        assert manager.active_account_id == new_id

    asyncio.run(scenario())


def test_add_account_raises_sign_in_cancelled_on_loopback_timeout(monkeypatch):
    async def scenario():
        app = FakeMsalApp()
        manager, _ = _make_manager(app)

        FakeLoopbackListener.outcome = LoopbackTimeout("no redirect arrived")
        monkeypatch.setattr(auth_manager, "LoopbackListener", FakeLoopbackListener)
        monkeypatch.setattr(auth_manager.webbrowser, "open", lambda *a, **k: True)

        with pytest.raises(SignInCancelled):
            await manager.add_account()

        assert manager.accounts == {}

    asyncio.run(scenario())


def test_add_account_raises_sign_in_cancelled_on_error_shaped_redirect(monkeypatch):
    async def scenario():
        app = FakeMsalApp()
        manager, _ = _make_manager(app)

        FakeLoopbackListener.outcome = LoopbackResult(
            query_params={"error": "access_denied", "state": "the-expected-state"}
        )
        monkeypatch.setattr(auth_manager, "LoopbackListener", FakeLoopbackListener)
        monkeypatch.setattr(auth_manager.webbrowser, "open", lambda *a, **k: True)

        with pytest.raises(SignInCancelled):
            await manager.add_account()

    asyncio.run(scenario())


# --- 5.2: switch_active_account -------------------------------------------------------------------


def test_switch_active_account_cancels_outgoing_group_and_updates_active_id():
    async def scenario():
        manager, task_registry = _make_manager()
        _seeded_account(manager, "acct-a")
        _seeded_account(manager, "acct-b")
        manager.active_account_id = "acct-a"

        await manager.switch_active_account("acct-b")

        assert task_registry.cancel_group_calls == ["acct-a"]
        assert manager.active_account_id == "acct-b"

    asyncio.run(scenario())


def test_switch_active_account_to_already_active_is_a_noop():
    async def scenario():
        manager, task_registry = _make_manager()
        _seeded_account(manager, "acct-a")
        manager.active_account_id = "acct-a"

        await manager.switch_active_account("acct-a")

        assert task_registry.cancel_group_calls == []
        assert manager.active_account_id == "acct-a"

    asyncio.run(scenario())


# --- active-account listeners (tasks.md 3.1, add-cloudpc-enumeration) ---------------------------


def test_switch_active_account_notifies_listeners_after_cancelling_the_old_group():
    async def scenario():
        manager, task_registry = _make_manager()
        _seeded_account(manager, "acct-a")
        _seeded_account(manager, "acct-b")
        manager.active_account_id = "acct-a"

        calls = []
        # Record whether cancel_group had already run when the listener fires -- the notification
        # must happen AFTER cancellation, not before (a listener that schedules new work under
        # task_registry must never race the old group's cancellation).
        manager.add_active_account_listener(
            lambda account_id: calls.append((account_id, list(task_registry.cancel_group_calls)))
        )

        await manager.switch_active_account("acct-b")

        assert calls == [("acct-b", ["acct-a"])]

    asyncio.run(scenario())


def test_switch_active_account_to_already_active_does_not_notify():
    async def scenario():
        manager, _ = _make_manager()
        _seeded_account(manager, "acct-a")
        manager.active_account_id = "acct-a"
        calls = []
        manager.add_active_account_listener(calls.append)

        await manager.switch_active_account("acct-a")

        assert calls == []

    asyncio.run(scenario())


def test_add_account_notifies_listeners_only_for_the_first_account(monkeypatch):
    async def scenario():
        app = FakeMsalApp()
        manager, _ = _make_manager(app)
        monkeypatch.setattr(auth_manager, "LoopbackListener", FakeLoopbackListener)
        monkeypatch.setattr(auth_manager.webbrowser, "open", lambda *a, **k: True)

        calls = []
        manager.add_active_account_listener(calls.append)

        app.acquire_by_code_flow_result = {
            "access_token": FAKE_ACCESS_TOKEN,
            "id_token_claims": {"oid": "oid-1", "tid": "tid-1", "preferred_username": "a@contoso.com"},
        }
        FakeLoopbackListener.outcome = LoopbackResult(query_params={"code": "c1", "state": "the-expected-state"})
        first_id = await manager.add_account()
        assert calls == [first_id]

        app.acquire_by_code_flow_result = {
            "access_token": FAKE_ACCESS_TOKEN,
            "id_token_claims": {"oid": "oid-2", "tid": "tid-1", "preferred_username": "b@contoso.com"},
        }
        FakeLoopbackListener.outcome = LoopbackResult(query_params={"code": "c2", "state": "the-expected-state"})
        await manager.add_account()
        # Still just the one call from the first account -- adding a second must not notify.
        assert calls == [first_id]

    asyncio.run(scenario())


def test_active_account_listener_exception_does_not_break_switch(caplog):
    async def scenario():
        manager, _ = _make_manager()
        _seeded_account(manager, "acct-a")
        _seeded_account(manager, "acct-b")
        manager.active_account_id = "acct-a"
        manager.add_active_account_listener(lambda account_id: (_ for _ in ()).throw(RuntimeError("boom")))

        with caplog.at_level(logging.ERROR):
            await manager.switch_active_account("acct-b")  # must not raise

        assert manager.active_account_id == "acct-b"

    asyncio.run(scenario())


# --- 5.3: sign_out -----------------------------------------------------------------------------


def test_sign_out_removes_only_target_account_and_reassigns_active_id():
    async def scenario():
        app = FakeMsalApp()
        app.accounts["acct-a"] = {"home_account_id": "acct-a", "username": "a@contoso.com"}
        app.accounts["acct-b"] = {"home_account_id": "acct-b", "username": "b@contoso.com"}

        manager, _ = _make_manager(app)
        _seeded_account(manager, "acct-a")
        _seeded_account(manager, "acct-b")
        manager.active_account_id = "acct-a"

        await manager.sign_out("acct-a")

        assert "acct-a" not in manager.accounts
        assert "acct-a" not in app.accounts
        assert "acct-b" in manager.accounts
        assert "acct-b" in app.accounts
        assert manager.active_account_id == "acct-b"
        assert len(app.removed_accounts) == 1
        assert app.removed_accounts[0]["home_account_id"] == "acct-a"

    asyncio.run(scenario())


def test_sign_out_of_non_active_account_leaves_active_id_untouched():
    async def scenario():
        app = FakeMsalApp()
        app.accounts["acct-a"] = {"home_account_id": "acct-a", "username": "a@contoso.com"}
        app.accounts["acct-b"] = {"home_account_id": "acct-b", "username": "b@contoso.com"}

        manager, _ = _make_manager(app)
        _seeded_account(manager, "acct-a")
        _seeded_account(manager, "acct-b")
        manager.active_account_id = "acct-a"

        await manager.sign_out("acct-b")

        assert manager.active_account_id == "acct-a"
        assert "acct-b" not in manager.accounts

    asyncio.run(scenario())


# --- 4.2 / state-driving: acquire_token_silently error paths --------------------------------------


def test_acquire_token_silently_raises_reauth_required_and_drives_state():
    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-invalid-grant"
        app.accounts[home_account_id] = {"home_account_id": home_account_id, "username": "a@contoso.com"}
        app.silent_result_factory = lambda scopes, account: {
            "error": "invalid_grant",
            "error_description": "AADSTS70008: refresh token expired",
        }

        manager, _ = _make_manager(app)
        account_state = _seeded_account(manager, home_account_id)
        assert account_state.state is AuthState.ACTIVE

        with pytest.raises(ReauthRequiredError) as excinfo:
            await manager.acquire_token_silently(home_account_id, ["Scope.Read"])

        assert excinfo.value.home_account_id == home_account_id
        assert account_state.state is AuthState.REAUTH_REQUIRED

    asyncio.run(scenario())


def test_acquire_token_silently_raises_device_ca_blocked_and_drives_state():
    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-device-ca"
        app.accounts[home_account_id] = {"home_account_id": home_account_id, "username": "a@contoso.com"}
        app.silent_result_factory = lambda scopes, account: {
            "error": "invalid_grant",
            "error_codes": [53000],
            "error_description": "AADSTS53000: device is not compliant",
        }

        manager, _ = _make_manager(app)
        account_state = _seeded_account(manager, home_account_id)

        with pytest.raises(DeviceCaBlockedError) as excinfo:
            await manager.acquire_token_silently(home_account_id, ["Scope.Read"])

        assert excinfo.value.home_account_id == home_account_id
        assert account_state.state is AuthState.DEVICE_CA_BLOCKED

    asyncio.run(scenario())


def test_acquire_token_silently_short_circuits_for_an_already_blocked_account():
    # Once DEVICE_CA_BLOCKED, spec.md section 6.5 says "never retried" -- a second call must not
    # make another MSAL/network call at all, not merely leave the state pinned afterward.
    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-device-ca-repeat"
        app.accounts[home_account_id] = {"home_account_id": home_account_id, "username": "a@contoso.com"}
        app.silent_result_factory = lambda scopes, account: {
            "error": "invalid_grant",
            "error_codes": [53000],
            "error_description": "AADSTS53000: device is not compliant",
        }

        manager, _ = _make_manager(app)
        account_state = _seeded_account(manager, home_account_id)

        with pytest.raises(DeviceCaBlockedError):
            await manager.acquire_token_silently(home_account_id, ["Scope.Read"])
        assert app.silent_calls == 1

        with pytest.raises(DeviceCaBlockedError):
            await manager.acquire_token_silently(home_account_id, ["Scope.Read"])
        assert app.silent_calls == 1  # no second MSAL call
        assert account_state.state is AuthState.DEVICE_CA_BLOCKED

    asyncio.run(scenario())


def test_reauth_from_banner_refuses_a_device_ca_blocked_account(monkeypatch):
    # A ReauthRequired banner should never be shown for a DEVICE_CA_BLOCKED account per spec.md's
    # own scenario, but if it is ever called anyway (a stale reference, a UI bug, a race), it must
    # raise the module's own documented DeviceCaBlockedError -- not silently reopen a terminal
    # account by starting a brand-new interactive flow.
    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-device-ca-reauth"
        app.accounts[home_account_id] = {"home_account_id": home_account_id, "username": "a@contoso.com"}

        manager, _ = _make_manager(app)
        account_state = _seeded_account(manager, home_account_id)
        account_state.apply_error(
            auth_manager.ClassifiedMsalError(category=auth_manager.MsalErrorCategory.DEVICE_CA_BLOCKED)
        )
        assert account_state.state is AuthState.DEVICE_CA_BLOCKED

        monkeypatch.setattr(auth_manager, "LoopbackListener", FakeLoopbackListener)
        monkeypatch.setattr(auth_manager.webbrowser, "open", lambda *a, **k: True)

        with pytest.raises(DeviceCaBlockedError) as excinfo:
            await manager.reauth_from_banner(home_account_id)

        assert excinfo.value.home_account_id == home_account_id
        # Still blocked -- not moved to INTERACTIVE_AUTH, and no interactive flow was attempted.
        assert account_state.state is AuthState.DEVICE_CA_BLOCKED
        assert app.initiate_flow_calls == []

    asyncio.run(scenario())


def test_acquire_token_silently_raises_claims_challenge_and_drives_state():
    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-claims"
        app.accounts[home_account_id] = {"home_account_id": home_account_id, "username": "a@contoso.com"}
        app.silent_result_factory = lambda scopes, account: {
            "error": "interaction_required",
            "claims": FAKE_CLAIMS,
        }

        manager, _ = _make_manager(app)
        account_state = _seeded_account(manager, home_account_id)

        with pytest.raises(ClaimsChallengeError) as excinfo:
            await manager.acquire_token_silently(home_account_id, ["Scope.Read"])

        assert excinfo.value.home_account_id == home_account_id
        assert excinfo.value.claims == FAKE_CLAIMS
        assert account_state.state is AuthState.INTERACTIVE_AUTH
        assert account_state.pending_claims == FAKE_CLAIMS

    asyncio.run(scenario())


def test_acquire_token_silently_raises_offline_no_cached_token_on_network_exception():
    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-offline"
        app.accounts[home_account_id] = {"home_account_id": home_account_id, "username": "a@contoso.com"}
        app.silent_exception_factory = lambda: OSError("network is unreachable")

        manager, _ = _make_manager(app)
        account_state = _seeded_account(manager, home_account_id)
        assert account_state.state is AuthState.ACTIVE

        with pytest.raises(OfflineNoCachedTokenError) as excinfo:
            await manager.acquire_token_silently(home_account_id, ["Scope.Read"])

        assert excinfo.value.home_account_id == home_account_id
        assert account_state.state is AuthState.OFFLINE

    asyncio.run(scenario())


def test_acquire_token_silently_marks_account_active_on_success():
    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-success"
        app.accounts[home_account_id] = {"home_account_id": home_account_id, "username": "a@contoso.com"}
        app.silent_result_factory = lambda scopes, account: {"access_token": FAKE_ACCESS_TOKEN}

        manager, _ = _make_manager(app)
        account_state = _seeded_account(manager, home_account_id)

        token = await manager.acquire_token_silently(home_account_id, ["Scope.Read"])

        assert token == FAKE_ACCESS_TOKEN
        assert account_state.state is AuthState.ACTIVE

    asyncio.run(scenario())


# --- log redaction discipline (spec.md section 10.7) -----------------------------------------------


def test_acquire_token_silently_never_logs_token_or_claims_values(caplog):
    async def scenario():
        app = FakeMsalApp()

        success_id = "acct-log-success"
        app.accounts[success_id] = {"home_account_id": success_id, "username": "a@contoso.com"}

        manager, _ = _make_manager(app)
        success_state = _seeded_account(manager, success_id)

        app.silent_result_factory = lambda scopes, account: {"access_token": FAKE_ACCESS_TOKEN}
        with caplog.at_level(logging.DEBUG):
            token = await manager.acquire_token_silently(success_id, ["Scope.Read"])
        assert token == FAKE_ACCESS_TOKEN

        claims_id = "acct-log-claims"
        app.accounts[claims_id] = {"home_account_id": claims_id, "username": "b@contoso.com"}
        _seeded_account(manager, claims_id)
        app.silent_result_factory = lambda scopes, account: {
            "error": "interaction_required",
            "claims": FAKE_CLAIMS,
        }
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(ClaimsChallengeError):
                await manager.acquire_token_silently(claims_id, ["Scope.Read"])

        assert success_state.state is AuthState.ACTIVE

    asyncio.run(scenario())

    # Checked as booleans, not `assert FAKE_ACCESS_TOKEN not in caplog.text` directly: pytest's
    # assertion-rewriting prints both operands of a failed `in`/`not in` comparison, so a real
    # redaction regression would otherwise leak the very secret this test exists to catch straight
    # into the test/CI failure output. A plain boolean assertion never echoes the compared values.
    token_leaked = FAKE_ACCESS_TOKEN in caplog.text
    claims_leaked = FAKE_CLAIMS in caplog.text
    assert not token_leaked, "an access token value leaked into the log output"
    assert not claims_leaked, "a claims value leaked into the log output"


# --- fix-account-lifecycle (audit F-04/F-05/F-13/F-19) --------------------------------------------


def test_sign_out_destroys_the_accounts_task_group():
    """sign_out must tear down the account's background work (FR-3-AC-3, audit F-05's
    orphaned-poll-loop fix) -- destroy_group called exactly once, for that account, after the
    cache removal."""

    async def scenario():
        app = FakeMsalApp()
        app.accounts["acct-signout-group"] = {
            "home_account_id": "acct-signout-group", "username": "user@contoso.com"
        }
        manager, task_registry = _make_manager(app)
        _seeded_account(manager, "acct-signout-group")

        await manager.sign_out("acct-signout-group")

        assert task_registry.destroy_group_calls == ["acct-signout-group"]
        assert "acct-signout-group" not in manager.accounts

    asyncio.run(scenario())


def test_sign_out_scheduled_inside_the_accounts_own_group_still_completes():
    """A sign_out running INSIDE the account's own task group cancels itself via destroy_group --
    the method must still complete its removal work (the self-cancellation is only deliverable at
    a later await), not deadlock or leave state half-removed."""
    from winonlinux.task_registry import TaskRegistry

    async def scenario():
        app = FakeMsalApp()
        app.accounts["acct-inside"] = {
            "home_account_id": "acct-inside", "username": "user@contoso.com"
        }
        registry = TaskRegistry()
        manager = AuthManager(task_registry=registry, client_id="test-client-id")
        manager._app = app
        _seeded_account(manager, "acct-inside")

        completed = {"flag": False}

        async def sign_out_from_inside():
            await manager.sign_out("acct-inside")
            completed["flag"] = True

        task = registry.get_or_create_group("acct-inside").create_task(sign_out_from_inside())
        # The task either completes normally (cancellation never delivered -- no await after
        # sign_out returns) or is marked cancelled at its end; the removal must have happened
        # either way and nothing may deadlock.
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.CancelledError:
            pass

        assert completed["flag"] is True
        assert "acct-inside" not in manager.accounts

    asyncio.run(scenario())


def test_acquire_token_silently_unknown_account_raises_typed_error():
    """A removed/unknown account raises AccountUnknownError (an AuthError), never a bare
    KeyError -- the audit F-05 poll-loop failure shape."""
    from winonlinux.auth_manager import AccountUnknownError

    async def scenario():
        manager, _ = _make_manager()
        with pytest.raises(AccountUnknownError):
            await manager.acquire_token_silently("acct-never-signed-in", ["CloudPC.Read.All"])

    asyncio.run(scenario())


def test_reauth_from_banner_unknown_account_raises_typed_error():
    from winonlinux.auth_manager import AccountUnknownError

    async def scenario():
        manager, _ = _make_manager()
        with pytest.raises(AccountUnknownError):
            await manager.reauth_from_banner("acct-never-signed-in")

    asyncio.run(scenario())


def test_unstarted_manager_refuses_typed_with_no_socket_opened(monkeypatch):
    """Before start() completes (incl. after a KeyringUnavailable refusal, D-2), every public
    operation raises AuthManagerNotStarted BEFORE any side effect -- in particular no loopback
    listener is ever constructed (audit F-13)."""
    from winonlinux.auth_manager import AuthManagerNotStarted

    constructed: list[object] = []

    class RecordingListener:
        def __init__(self, **kwargs):
            constructed.append(self)

    monkeypatch.setattr(auth_manager, "LoopbackListener", RecordingListener)

    async def scenario():
        manager = AuthManager(task_registry=FakeTaskRegistry(), client_id="test-client-id")
        # start() never called: _app is None, exactly the post-KeyringUnavailable shape.
        with pytest.raises(AuthManagerNotStarted):
            await manager.add_account()
        with pytest.raises(AuthManagerNotStarted):
            await manager.reauth_from_banner("any-account")
        with pytest.raises(AuthManagerNotStarted):
            await manager.acquire_token_silently("any-account", ["CloudPC.Read.All"])
        with pytest.raises(AuthManagerNotStarted):
            await manager.sign_out("any-account")
        with pytest.raises(AuthManagerNotStarted):
            await manager.switch_active_account("any-account")

    asyncio.run(scenario())
    assert constructed == []


def test_silent_acquisition_passes_through_silent_refresh_and_returns_to_active():
    """The section 6.4 SilentRefresh state is real and observable during the MSAL call
    (audit F-19), and a success lands back in ACTIVE."""

    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-silent-refresh"
        app.accounts[home_account_id] = {
            "home_account_id": home_account_id, "username": "user@contoso.com"
        }
        manager, _ = _make_manager(app)
        account_state = _seeded_account(manager, home_account_id)

        observed_states: list[AuthState] = []

        def result_factory(scopes, account):
            observed_states.append(account_state.state)
            return {"access_token": FAKE_ACCESS_TOKEN}

        app.silent_result_factory = result_factory

        token = await manager.acquire_token_silently(home_account_id, ["CloudPC.Read.All"])

        assert token == FAKE_ACCESS_TOKEN
        assert observed_states == [AuthState.SILENT_REFRESH]
        assert account_state.state is AuthState.ACTIVE

    asyncio.run(scenario())


def test_offline_account_recovers_through_silent_refresh():
    """OFFLINE -> SILENT_REFRESH -> ACTIVE: the recovery path a later successful poll tick
    drives (section 6.4; audit F-19)."""

    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-offline-recovery"
        app.accounts[home_account_id] = {
            "home_account_id": home_account_id, "username": "user@contoso.com"
        }
        manager, _ = _make_manager(app)
        account_state = _seeded_account(manager, home_account_id)
        account_state.mark_offline()
        assert account_state.state is AuthState.OFFLINE

        app.silent_result_factory = lambda scopes, account: {"access_token": FAKE_ACCESS_TOKEN}

        token = await manager.acquire_token_silently(home_account_id, ["CloudPC.Read.All"])

        assert token == FAKE_ACCESS_TOKEN
        assert account_state.state is AuthState.ACTIVE

    asyncio.run(scenario())


# --- add-state-change-listeners (audit F-08/F-20): auth-state observers, user messages ------------


def test_background_invalid_grant_fires_auth_state_listener_with_reauth_required():
    """The section 4.4 per-account ReauthRequired banner signal: a background silent acquisition
    classifying INVALID_GRANT notifies (home_account_id, REAUTH_REQUIRED) -- no polling needed."""

    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-listener-invalid-grant"
        app.accounts[home_account_id] = {
            "home_account_id": home_account_id, "username": "user@contoso.com"
        }
        app.silent_result_factory = lambda scopes, account: {"error": "invalid_grant"}
        manager, _ = _make_manager(app)
        _seeded_account(manager, home_account_id)

        events: list[tuple[str, AuthState]] = []
        manager.add_auth_state_listener(lambda acct, state: events.append((acct, state)))

        with pytest.raises(ReauthRequiredError):
            await manager.acquire_token_silently(home_account_id, ["CloudPC.Read.All"])

        assert (home_account_id, AuthState.REAUTH_REQUIRED) in events
        # The refresh itself was observable too (F-19's SILENT_REFRESH pass-through).
        assert (home_account_id, AuthState.SILENT_REFRESH) in events

    asyncio.run(scenario())


def test_device_ca_blocked_fires_listener_exactly_once():
    """Entering the terminal state notifies once; later acquisitions short-circuit without any
    further transition or notification (spec.md section 6.5)."""

    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-listener-blocked"
        app.accounts[home_account_id] = {
            "home_account_id": home_account_id, "username": "user@contoso.com"
        }
        app.silent_result_factory = lambda scopes, account: {
            "error": "interaction_required", "error_codes": [53000]
        }
        manager, _ = _make_manager(app)
        _seeded_account(manager, home_account_id)

        events: list[tuple[str, AuthState]] = []
        manager.add_auth_state_listener(lambda acct, state: events.append((acct, state)))

        with pytest.raises(DeviceCaBlockedError):
            await manager.acquire_token_silently(home_account_id, ["CloudPC.Read.All"])
        with pytest.raises(DeviceCaBlockedError):
            await manager.acquire_token_silently(home_account_id, ["CloudPC.Read.All"])

        blocked_events = [e for e in events if e[1] is AuthState.DEVICE_CA_BLOCKED]
        assert blocked_events == [(home_account_id, AuthState.DEVICE_CA_BLOCKED)]

    asyncio.run(scenario())


def test_sign_out_fires_listener_with_signed_out():
    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-listener-signout"
        app.accounts[home_account_id] = {
            "home_account_id": home_account_id, "username": "user@contoso.com"
        }
        manager, _ = _make_manager(app)
        _seeded_account(manager, home_account_id)

        events: list[tuple[str, AuthState]] = []
        manager.add_auth_state_listener(lambda acct, state: events.append((acct, state)))

        await manager.sign_out(home_account_id)

        assert (home_account_id, AuthState.SIGNED_OUT) in events

    asyncio.run(scenario())


def test_raising_auth_state_listener_does_not_break_transition_or_other_listeners():
    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-listener-raises"
        app.accounts[home_account_id] = {
            "home_account_id": home_account_id, "username": "user@contoso.com"
        }
        app.silent_result_factory = lambda scopes, account: {"error": "invalid_grant"}
        manager, _ = _make_manager(app)
        account_state = _seeded_account(manager, home_account_id)

        def bad_listener(acct, state):
            raise RuntimeError("listener bug")

        good_events: list[tuple[str, AuthState]] = []
        manager.add_auth_state_listener(bad_listener)
        manager.add_auth_state_listener(lambda acct, state: good_events.append((acct, state)))

        with pytest.raises(ReauthRequiredError):
            await manager.acquire_token_silently(home_account_id, ["CloudPC.Read.All"])

        # The transition happened and the second listener still heard it.
        assert account_state.state is AuthState.REAUTH_REQUIRED
        assert (home_account_id, AuthState.REAUTH_REQUIRED) in good_events

    asyncio.run(scenario())


def test_remove_auth_state_listener_stops_notifications():
    async def scenario():
        app = FakeMsalApp()
        home_account_id = "acct-listener-removed"
        app.accounts[home_account_id] = {
            "home_account_id": home_account_id, "username": "user@contoso.com"
        }
        manager, _ = _make_manager(app)

        events: list[tuple[str, AuthState]] = []
        listener = lambda acct, state: events.append((acct, state))  # noqa: E731
        manager.add_auth_state_listener(listener)
        manager.remove_auth_state_listener(listener)
        manager.remove_auth_state_listener(listener)  # second removal is a documented no-op

        _seeded_account(manager, home_account_id)  # fires mark_active
        assert events == []

    asyncio.run(scenario())


def test_every_auth_error_type_carries_a_clean_user_message():
    """Section 9 messaging rules (audit F-20): every AuthError type carries a non-empty
    user_message with no raw protocol detail (AADSTS codes, HTTP statuses)."""
    from winonlinux.auth_manager import _USER_MESSAGES

    for exc_type, message in _USER_MESSAGES.items():
        assert issubclass(exc_type, auth_manager.AuthError)
        assert exc_type.user_message == message
        assert message.strip(), f"{exc_type.__name__} has an empty user_message"
        assert "AADSTS" not in message
        assert "HTTP" not in message

    # An instance carries it too, and the raw MSAL detail is namespaced for logs only.
    from winonlinux.auth_errors import MsalErrorCategory

    err = auth_manager.InteractiveSignInFailedError(
        MsalErrorCategory.UNKNOWN, "AADSTS900000: something raw"
    )
    assert "AADSTS" not in err.user_message
    assert err.raw_error_description_for_logs == "AADSTS900000: something raw"
    assert not hasattr(err, "raw_error_description")


# --- fix-shutdown-loop-hygiene (audit F-09): per-manager serializer loop affinity -----------------


def test_two_managers_in_two_loops_contend_their_own_serializers_without_cross_loop_error():
    """The audit's Python 3.12 reproducer, generalized: each AuthManager owns its serializer, so
    running contended acquisitions in two managers across two fresh event loops raises no
    'bound to a different event loop' RuntimeError (the old module singleton did)."""

    def run_contended_acquisitions() -> None:
        app = FakeMsalApp()
        home_account_id = "acct-loop-affinity"
        app.accounts[home_account_id] = {
            "home_account_id": home_account_id, "username": "user@contoso.com"
        }
        app.silent_sleep_seconds = 0.02  # long enough that the serializer genuinely contends
        app.silent_result_factory = lambda scopes, account: {"access_token": FAKE_ACCESS_TOKEN}
        manager, _ = _make_manager(app)
        _seeded_account(manager, home_account_id)

        async def scenario():
            # Different scopes bypass the single-flight dedup, so both callers reach the
            # cache-write serializer concurrently and contend its lock.
            await asyncio.gather(
                manager.acquire_token_silently(home_account_id, ["Scope.A"]),
                manager.acquire_token_silently(home_account_id, ["Scope.B"]),
            )

        asyncio.run(scenario())

    run_contended_acquisitions()  # loop 1, manager 1
    run_contended_acquisitions()  # loop 2, manager 2 -- must not see loop 1's lock


# --- add-audit-test-coverage (audit F-22/F-23): authority pin, placeholder WARNING ----------------


class _RecordingMsalConstructor:
    """Records every msal.PublicClientApplication(...) construction start() performs."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, client_id, *, authority=None, token_cache=None):
        self.calls.append(
            {"client_id": client_id, "authority": authority, "token_cache": token_cache}
        )
        return FakeMsalApp()


def test_authority_derives_only_from_constructor_arguments(monkeypatch):
    """Pins that the authority is composed from constructor args alone -- hostile environment
    variables and state-file-shaped inputs have no path into it (an authority override would be
    a token-exfiltration vector; audit F-22/D-10 note, spec.md section 6.1)."""
    recording = _RecordingMsalConstructor()
    monkeypatch.setattr(auth_manager.msal, "PublicClientApplication", recording)
    monkeypatch.setattr(auth_manager, "build_persisted_cache", lambda: object())
    # Hostile environment: nothing in AuthManager reads any of these.
    monkeypatch.setenv("WINONLINUX_AUTHORITY", "https://evil.example/tenant")
    monkeypatch.setenv("MSAL_AUTHORITY", "https://evil.example/tenant")
    monkeypatch.setenv("AUTHORITY", "https://evil.example/tenant")

    async def scenario():
        manager = AuthManager(task_registry=FakeTaskRegistry(), client_id="test-client-id")
        await manager.start()
        manager_custom = AuthManager(
            task_registry=FakeTaskRegistry(), client_id="test-client-id", tenant="contoso.com"
        )
        await manager_custom.start()

    asyncio.run(scenario())

    assert [call["authority"] for call in recording.calls] == [
        "https://login.microsoftonline.com/organizations",
        "https://login.microsoftonline.com/contoso.com",
    ]


def test_placeholder_client_id_warning_fires_exactly_once_across_two_starts(monkeypatch, caplog):
    """Pins the D-19 placeholder WARNING (audit F-23, DECIDED-BUT-UNVERIFIED's verifiable half):
    it fires on the first start() with the placeholder still in place, and only once
    process-wide."""
    monkeypatch.setattr(auth_manager.msal, "PublicClientApplication", _RecordingMsalConstructor())
    monkeypatch.setattr(auth_manager, "build_persisted_cache", lambda: object())
    monkeypatch.setattr(auth_manager, "_placeholder_warning_emitted", False)

    async def scenario():
        with caplog.at_level(logging.WARNING, logger="winonlinux.auth_manager"):
            first = AuthManager(task_registry=FakeTaskRegistry())  # placeholder client id
            await first.start()
            second = AuthManager(task_registry=FakeTaskRegistry())
            await second.start()

    asyncio.run(scenario())

    warnings = [r for r in caplog.records if "PLACEHOLDER client ID" in r.getMessage()]
    assert len(warnings) == 1
