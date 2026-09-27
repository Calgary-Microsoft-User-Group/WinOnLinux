"""Unit tests for winonlinux.cloudpc_actions (add-cloudpc-actions change; FR-5, §7.2/§7.4).

Pure asyncio, ``asyncio.run`` convention. ``requests.post`` is monkeypatched with fixture-shaped
FakeResponses per §13.3 (accepted 204, mutated beta shape, 403 unconsented, 429 with
Retry-After); no network. The FakeAuthManager mints decodable JWTs so the D-7 wids evaluation
runs against realistic token shapes.
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
import requests

from winonlinux import cloudpc_actions as actions_module
from winonlinux.cloudpc_actions import (
    CONTRACT_ERROR_REASON,
    Accepted,
    CloudPcActionService,
    ConsentRequired,
    ContractError,
    Failed,
    Throttled,
)

pytestmark = pytest.mark.unit

ACCOUNT = "acct-actions-test"
WINDOWS365_ADMIN_WID = "11451d60-acb2-45eb-a7d6-43d0f0125c13"


def _fake_jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"fakehdr.{payload}.fakesig"


class FakeAuthManager:
    def __init__(self, token: str = "") -> None:
        self.token = token or _fake_jwt({"sub": "user"})
        self.acquire_calls = 0
        self.accounts = {ACCOUNT: SimpleNamespace(login_hint="user@contoso.com")}

    async def acquire_token_silently(self, home_account_id: str, scopes: list[str]) -> str:
        self.acquire_calls += 1
        return self.token


class FakeProvider:
    def __init__(self) -> None:
        self.refresh_after_action_calls: list[str] = []

    async def refresh_after_action(self, home_account_id: str):
        self.refresh_after_action_calls.append(home_account_id)


class FakeGroup:
    def __init__(self) -> None:
        self.created: list = []

    def create_task(self, coro, name=None):
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self.created.append(task)
        return task


class FakeTaskRegistry:
    def __init__(self) -> None:
        self.group = FakeGroup()

    def get_or_create_group(self, account_id=None):
        return self.group


class FakeResponse:
    def __init__(self, status_code: int, *, json_body=None, headers=None, text: str = "") -> None:
        self.status_code = status_code
        self._json_body = json_body
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._json_body is None:
            raise ValueError("no JSON body")
        return self._json_body


def _service(**kwargs) -> tuple[CloudPcActionService, FakeAuthManager, FakeProvider, FakeTaskRegistry]:
    auth = kwargs.pop("auth", FakeAuthManager())
    provider = FakeProvider()
    registry = FakeTaskRegistry()
    service = CloudPcActionService(
        auth_manager=auth, cloudpc_provider=provider, task_registry=registry, **kwargs
    )
    return service, auth, provider, registry


async def _settle(registry: FakeTaskRegistry) -> None:
    for task in registry.group.created:
        await task


# --- invocation outcomes (fixtures per §13.3) -----------------------------------------------------


def test_accepted_204_schedules_immediate_refresh_and_tokens_precede_calls(monkeypatch):
    """FR-5-AC-1 + FR-4-AC-1 + D-8: 204 -> Accepted, silent acquisition preceded the POST, and
    an immediate refresh_after_action was scheduled under the account's group."""
    posts: list[tuple[str, dict | None]] = []

    def fake_post(url, headers=None, timeout=None, json=None):
        posts.append((url, json))
        assert headers["Authorization"].startswith("Bearer ")
        return FakeResponse(204)

    monkeypatch.setattr(requests, "post", fake_post)

    async def scenario():
        service, auth, provider, registry = _service()
        outcome = await service.invoke_action(ACCOUNT, "cpc-1", "restart")
        await _settle(registry)
        return outcome, auth, provider, posts

    outcome, auth, provider, recorded = asyncio.run(scenario())

    assert isinstance(outcome, Accepted)
    assert auth.acquire_calls >= 1  # acquisition before the call (FR-4-AC-1)
    assert recorded[0][0] == "https://graph.microsoft.com/beta/me/cloudPCs/cpc-1/reboot"
    assert provider.refresh_after_action_calls == [ACCOUNT]  # D-8 immediate refresh


def test_rename_carries_the_display_name_payload(monkeypatch):
    posts: list[tuple[str, dict | None]] = []
    monkeypatch.setattr(
        requests,
        "post",
        lambda url, headers=None, timeout=None, json=None: (
            posts.append((url, json)),
            FakeResponse(204),
        )[1],
    )

    async def scenario():
        service, *_rest, registry = _service()
        outcome = await service.invoke_action(
            ACCOUNT, "cpc-1", "rename", payload={"displayName": "Finance PC"}
        )
        await _settle(registry)
        return outcome

    assert isinstance(asyncio.run(scenario()), Accepted)
    assert posts[0][0].endswith("/rename")
    assert posts[0][1] == {"displayName": "Finance PC"}


def test_403_maps_to_consent_required(monkeypatch):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **k: FakeResponse(403, json_body={"error": {"code": "AccessDenied"}}),
    )

    async def scenario():
        service, *_ = _service()
        return await service.invoke_action(ACCOUNT, "cpc-1", "restart")

    outcome = asyncio.run(scenario())
    assert isinstance(outcome, ConsentRequired)
    assert "administrator" in outcome.user_message


def test_mutated_2xx_shape_disables_only_that_action(monkeypatch):
    """FR-5-AC-5: a 200-with-body where 204 is documented is a contract change -- that action
    (and only it) is disabled for the session with the exact stated reason."""
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: FakeResponse(200, json_body={"surprise": True})
    )

    async def scenario():
        service, *_ = _service()
        outcome = await service.invoke_action(ACCOUNT, "cpc-1", "restart")
        return service, outcome

    service, outcome = asyncio.run(scenario())

    assert isinstance(outcome, ContractError)
    assert outcome.user_message == "Action unavailable — Microsoft API change"
    enabled, reason = service.action_gate("restart")
    assert not enabled and reason == CONTRACT_ERROR_REASON
    # Only the affected action: the others stay enabled.
    assert service.action_gate("troubleshoot") == (True, None)
    assert service.action_gate("rename") == (True, None)


def test_404_treated_as_contract_change(monkeypatch):
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: FakeResponse(404, json_body={"error": "gone"})
    )

    async def scenario():
        service, *_ = _service()
        return service, await service.invoke_action(ACCOUNT, "cpc-1", "troubleshoot")

    service, outcome = asyncio.run(scenario())
    assert isinstance(outcome, ContractError)
    assert service.action_gate("troubleshoot")[0] is False


def test_persistent_429_surfaces_throttled_with_retry_after(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **k: FakeResponse(429, headers={"Retry-After": "7"}, text="throttled"),
    )

    async def scenario():
        service, *_ = _service()
        return await service.invoke_action(ACCOUNT, "cpc-1", "restart")

    outcome = asyncio.run(scenario())
    assert isinstance(outcome, Throttled)
    assert outcome.retry_after_seconds == 7.0
    assert sleeps  # the shared backoff machinery retried before giving up (§9)


def test_network_failure_maps_to_failed_with_offline_wording(monkeypatch):
    def fake_post(*a, **k):
        raise requests.exceptions.ConnectionError("unreachable")

    monkeypatch.setattr(requests, "post", fake_post)

    async def scenario():
        service, *_ = _service()
        return await service.invoke_action(ACCOUNT, "cpc-1", "restart")

    outcome = asyncio.run(scenario())
    assert isinstance(outcome, Failed)
    assert "offline" in outcome.user_message.lower()
    assert "AADSTS" not in outcome.user_message and "HTTP" not in outcome.user_message


# --- reprovision: the structurally separate destructive path (FR-5-AC-2) --------------------------


def test_reprovision_requires_its_own_path_and_confirmation(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        requests, "post", lambda url, **k: (calls.append(url), FakeResponse(204))[1]
    )

    async def scenario():
        service, _auth, _provider, registry = _service()
        # The shared path refuses reprovision outright.
        with pytest.raises(ValueError):
            await service.invoke_action(ACCOUNT, "cpc-1", "reprovision")
        # Unconfirmed reprovision refuses BEFORE any network call.
        with pytest.raises(ValueError):
            await service.invoke_reprovision(ACCOUNT, "cpc-1", confirmed=False)
        assert calls == []
        # Confirmed reprovision issues exactly one call.
        outcome = await service.invoke_reprovision(ACCOUNT, "cpc-1", confirmed=True)
        await _settle(registry)
        return outcome

    outcome = asyncio.run(scenario())
    assert isinstance(outcome, Accepted)
    assert calls == ["https://graph.microsoft.com/beta/me/cloudPCs/cpc-1/reprovision"]


def test_reprovision_is_never_auto_retried(monkeypatch):
    """FR-5-AC-2/§10.6: a transient 429 (or 503) on reprovision surfaces immediately -- exactly
    one POST, no backoff, no re-issue of a wipe-and-rebuild."""
    calls: list[int] = []
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **k: (calls.append(1), FakeResponse(429, headers={"Retry-After": "2"}))[1],
    )

    async def scenario():
        service, *_ = _service()
        return await service.invoke_reprovision(ACCOUNT, "cpc-1", confirmed=True)

    outcome = asyncio.run(scenario())
    assert isinstance(outcome, Throttled)
    assert len(calls) == 1  # never retried


# --- gating: flag, national cloud, admin capability (§7.2, FR-5-AC-3/AC-6) ------------------------


def test_flag_off_disables_all_actions_with_reason_and_no_call(monkeypatch):
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("unreachable"))
    )

    async def scenario():
        service, *_ = _service(beta_actions_enabled=False)
        enabled, reason = service.action_gate("restart")
        outcome = await service.invoke_action(ACCOUNT, "cpc-1", "restart")
        return enabled, reason, outcome

    enabled, reason, outcome = asyncio.run(scenario())
    assert not enabled and "disabled in this build" in reason
    assert isinstance(outcome, Failed)  # defensive refusal, no network


def test_national_cloud_disables_fr5_wholesale():
    async def scenario():
        service, *_ = _service(authority_host="login.chinacloudapi.cn")
        return service

    service = asyncio.run(scenario())
    assert service.service_disabled_reason() is not None
    for action in ("restart", "rename", "troubleshoot", "reprovision"):
        enabled, reason = service.action_gate(action)
        assert not enabled
        assert "cloud environment" in reason


def test_admin_capability_from_wids_both_ways_and_unknown():
    async def scenario():
        admin = FakeAuthManager(token=_fake_jwt({"wids": [WINDOWS365_ADMIN_WID, "other-id"]}))
        plain = FakeAuthManager(token=_fake_jwt({"wids": ["not-an-admin-role"]}))
        no_claims = FakeAuthManager(token=_fake_jwt({"sub": "user"}))
        undecodable = FakeAuthManager(token="not-a-jwt-at-all")

        results = []
        for auth in (admin, plain, no_claims, undecodable):
            service, *_ = _service(auth=auth)
            results.append(await service.evaluate_admin_capability(ACCOUNT))
        return results

    admin_capable, plain_capable, unknown_capable, undecodable_capable = asyncio.run(scenario())
    assert admin_capable is True
    assert plain_capable is False
    assert unknown_capable is False  # capability-unknown -> not capable (FR-5-AC-3)
    assert undecodable_capable is False


def test_admin_capability_cached_per_token_and_reevaluated_on_change():
    async def scenario():
        auth = FakeAuthManager(token=_fake_jwt({"wids": []}))
        service, *_ = _service(auth=auth)
        first = await service.evaluate_admin_capability(ACCOUNT)
        # Token refresh rotates the token and now carries the admin wid: re-evaluated.
        auth.token = _fake_jwt({"wids": [WINDOWS365_ADMIN_WID]})
        second = await service.evaluate_admin_capability(ACCOUNT)
        return first, second

    first, second = asyncio.run(scenario())
    assert first is False and second is True


def test_admin_visibility_actions_are_never_invocable_via_gate():
    async def scenario():
        service, *_ = _service()
        return service.action_gate("restore"), service.action_gate("resize")

    restore, resize = asyncio.run(scenario())
    assert restore[0] is False and resize[0] is False  # visibility-only in this change
