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
    """Minimal stand-in for winonlinux.task_registry.TaskRegistry -- AuthManager only ever calls
    cancel_group() on it (switch_active_account), matching the real class's confirmed signature
    `cancel_group(self, account_id: AccountId | None) -> None`."""

    def __init__(self) -> None:
        self.cancel_group_calls: list[object] = []

    def cancel_group(self, account_id: object) -> None:
        self.cancel_group_calls.append(account_id)


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
    state = AccountAuthState(home_account_id, login_hint=login_hint)
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
