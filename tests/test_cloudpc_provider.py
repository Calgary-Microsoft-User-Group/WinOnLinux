"""Unit tests for winonlinux.cloudpc_provider (add-cloudpc-enumeration change, tasks.md 5.2-5.5).

Pure asyncio -- driven with plain ``asyncio.run``, matching tests/test_task_registry.py and
tests/test_graph_client.py's convention rather than a pytest-asyncio plugin.

``winonlinux.cloudpc_provider.graph_client.graph_get_json`` is monkeypatched directly in every
test here -- never ``requests``, which is entirely graph_client's own test's concern
(tests/test_graph_client.py), not this file's. The one exception is the cancellation test (5.5),
which uses a real ``winonlinux.task_registry.TaskRegistry`` because that is specifically what it
needs to prove.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from winonlinux import cloudpc_provider, graph_client
from winonlinux.cloudpc_provider import (
    CloudPcProvider,
    ConsentRequired,
    Empty,
    Enumerated,
    Failed,
    NoLicence,
)
from winonlinux.task_registry import TaskRegistry

pytestmark = pytest.mark.unit

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "graph"
FAKE_ACCOUNT = "acct-cloudpc-test"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


class DummyAuthManager:
    """Never actually consulted: every test here monkeypatches
    ``cloudpc_provider.graph_client.graph_get_json`` directly, so no code path ever reaches
    ``acquire_token_silently`` -- this stub exists only so ``CloudPcProvider`` has *something* to
    hold as ``auth_manager`` and pass through."""


def _make_provider(**kwargs) -> CloudPcProvider:
    return CloudPcProvider(
        auth_manager=DummyAuthManager(), task_registry=TaskRegistry(), **kwargs
    )


# --- 5.2 paging: all pages surfaced, no partial result (FR-1-AC-1) ------------------------------


def test_paging_follows_next_link_and_surfaces_every_entry_from_both_pages(monkeypatch):
    page1 = _load_fixture("cloudpcs_page1.json")
    page2 = _load_fixture("cloudpcs_page2.json")
    next_link = page1["@odata.nextLink"]
    calls: list[str] = []

    async def fake_graph_get_json(url, *, auth_manager, home_account_id, scopes, max_retries=5):
        calls.append(url)
        assert home_account_id == FAKE_ACCOUNT
        assert list(scopes) == ["CloudPC.Read.All"]
        if url == cloudpc_provider._CLOUDPCS_URL:
            return page1
        if url == next_link:
            return page2
        raise AssertionError(f"unexpected URL requested: {url}")

    monkeypatch.setattr(cloudpc_provider.graph_client, "graph_get_json", fake_graph_get_json)
    provider = _make_provider()

    result = asyncio.run(provider.refresh_now(FAKE_ACCOUNT))

    assert isinstance(result, Enumerated)
    expected_ids = {raw["id"] for raw in page1["value"]} | {raw["id"] for raw in page2["value"]}
    assert {entry.id for entry in result.entries} == expected_ids
    assert len(result.entries) == 3

    # Exactly two graph_get_json calls -- one per page, proving no page was skipped or
    # double-fetched.
    assert calls == [cloudpc_provider._CLOUDPCS_URL, next_link]

    # Fields carried through faithfully, including the raw status string unmodified.
    by_id = {entry.id: entry for entry in result.entries}
    for raw in page1["value"] + page2["value"]:
        entry = by_id[raw["id"]]
        assert entry.display_name == raw["displayName"]
        assert entry.status == raw["status"]
        assert entry.provisioning_type == raw["provisioningType"]
        assert entry.image_display_name == raw["imageDisplayName"]

    assert provider.last_result is result


def test_failure_on_page_two_of_three_never_surfaces_a_partial_enumerated(monkeypatch):
    """Explicit, distinct test for "partial pages are never surfaced as a complete result":
    page 1 of a would-be 3-page sequence succeeds, page 2 fails, page 3 is never reached -- the
    whole refresh must be Failed, not a partial Enumerated carrying only page 1's entry."""
    page1 = {
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/cloudPCs?$skiptoken=PAGE2",
        "value": [{"id": "p1-only", "displayName": "Page 1 Entry", "status": "provisioned"}],
    }
    calls = {"count": 0}

    async def fake_graph_get_json(url, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return page1
        # Page 2 fails -- page 3 (which would follow it) is never requested.
        raise graph_client.GraphNetworkError("connection dropped mid-paging")

    monkeypatch.setattr(cloudpc_provider.graph_client, "graph_get_json", fake_graph_get_json)
    provider = _make_provider()

    result = asyncio.run(provider.refresh_now(FAKE_ACCOUNT))

    assert isinstance(result, Failed)
    assert not isinstance(result, Enumerated)
    assert calls["count"] == 2
    # No prior successful enumeration existed, so previous_entries is empty -- specifically NOT
    # page 1's single entry, which must never leak through as a "successful" partial result.
    assert result.previous_entries == []
    assert provider.last_result is None


# --- 5.3 state distinction: Empty / NoLicence / Failed-keeps-previous / ConsentRequired ---------


def test_empty_collection_after_exhausted_paging_returns_empty_not_error(monkeypatch):
    async def fake_graph_get_json(url, **kwargs):
        return {"value": []}

    monkeypatch.setattr(cloudpc_provider.graph_client, "graph_get_json", fake_graph_get_json)
    provider = _make_provider()

    result = asyncio.run(provider.refresh_now(FAKE_ACCOUNT))

    assert isinstance(result, Empty)
    assert provider.last_result is result


def test_404_returns_no_licence_distinct_from_empty(monkeypatch):
    body = _load_fixture("cloudpcs_404.json")

    async def fake_graph_get_json(url, **kwargs):
        raise graph_client.GraphNotFound("not found", status_code=404, response_body=body)

    monkeypatch.setattr(cloudpc_provider.graph_client, "graph_get_json", fake_graph_get_json)
    provider = _make_provider()

    result = asyncio.run(provider.refresh_now(FAKE_ACCOUNT))

    assert isinstance(result, NoLicence)
    assert not isinstance(result, Empty)
    assert provider.last_result is result


def test_403_returns_consent_required_carrying_admin_consent_url(monkeypatch):
    body = _load_fixture("cloudpcs_403.json")

    async def fake_graph_get_json(url, **kwargs):
        raise graph_client.GraphConsentRequired(
            "consent required", status_code=403, response_body=body, admin_consent_url=None
        )

    monkeypatch.setattr(cloudpc_provider.graph_client, "graph_get_json", fake_graph_get_json)
    provider = _make_provider()

    result = asyncio.run(provider.refresh_now(FAKE_ACCOUNT))

    assert isinstance(result, ConsentRequired)
    # graph_client documents admin_consent_url as None in the common case -- mapped through as-is.
    assert result.admin_consent_url is None
    assert provider.last_result is result


def test_failed_refresh_keeps_previous_enumerated_entries(monkeypatch):
    page = _load_fixture("cloudpcs_page2.json")  # single page, no @odata.nextLink
    calls = {"count": 0}

    async def fake_graph_get_json(url, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return page
        raise graph_client.GraphNetworkError("network unreachable")

    monkeypatch.setattr(cloudpc_provider.graph_client, "graph_get_json", fake_graph_get_json)
    provider = _make_provider()

    first = asyncio.run(provider.refresh_now(FAKE_ACCOUNT))
    assert isinstance(first, Enumerated)

    second = asyncio.run(provider.refresh_now(FAKE_ACCOUNT))

    assert isinstance(second, Failed)
    assert isinstance(second.error, graph_client.GraphNetworkError)
    assert second.previous_entries == first.entries
    assert second.previous_entries != []
    # A Failed result must not overwrite last_result -- the next failure's previous_entries must
    # still be derivable from the last genuinely successful enumeration.
    assert provider.last_result is first


def test_four_result_types_are_distinct_via_isinstance(monkeypatch):
    """Four distinct result TYPES from four distinct inputs, asserted via isinstance -- exercising
    the isinstance-based dispatch design.md calls for, not string/attribute comparison."""

    async def make_result(behavior):
        async def fake_graph_get_json(url, **kwargs):
            return behavior() if callable(behavior) else behavior

        monkeypatch.setattr(cloudpc_provider.graph_client, "graph_get_json", fake_graph_get_json)
        provider = _make_provider()
        return await provider.refresh_now(FAKE_ACCOUNT)

    def raise_not_found():
        raise graph_client.GraphNotFound("nf", status_code=404)

    def raise_consent_required():
        raise graph_client.GraphConsentRequired("cr", status_code=403, admin_consent_url=None)

    empty_result = asyncio.run(make_result({"value": []}))
    no_licence_result = asyncio.run(make_result(raise_not_found))
    consent_required_result = asyncio.run(make_result(raise_consent_required))
    enumerated_result = asyncio.run(make_result(_load_fixture("cloudpcs_page2.json")))

    results = [empty_result, no_licence_result, consent_required_result, enumerated_result]
    types_seen = {type(r) for r in results}
    assert len(types_seen) == 4, "expected four genuinely distinct result types"
    assert isinstance(empty_result, Empty)
    assert isinstance(no_licence_result, NoLicence)
    assert isinstance(consent_required_result, ConsentRequired)
    assert isinstance(enumerated_result, Enumerated)


# --- 5.4 throttling: GraphThrottled maps to Failed like any other GraphError --------------------


def test_graph_throttled_maps_to_failed_keeping_previous_entries(monkeypatch):
    page = _load_fixture("cloudpcs_page2.json")
    calls = {"count": 0}

    async def fake_graph_get_json(url, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return page
        raise graph_client.GraphThrottled(
            "throttled past retry budget", status_code=429, retry_after_seconds=30.0
        )

    monkeypatch.setattr(cloudpc_provider.graph_client, "graph_get_json", fake_graph_get_json)
    provider = _make_provider()

    first = asyncio.run(provider.refresh_now(FAKE_ACCOUNT))
    assert isinstance(first, Enumerated)

    second = asyncio.run(provider.refresh_now(FAKE_ACCOUNT))

    assert isinstance(second, Failed)
    assert isinstance(second.error, graph_client.GraphThrottled)
    assert second.previous_entries == first.entries


def test_auth_error_from_silent_acquisition_propagates_out_of_refresh_now(monkeypatch):
    """AuthError subclasses must propagate completely uncaught -- refresh_now's typed-result model
    does not absorb them (that is a caller-level concern)."""

    class _FakeReauthRequiredError(Exception):
        pass

    async def fake_graph_get_json(url, **kwargs):
        raise _FakeReauthRequiredError("reauth required")

    monkeypatch.setattr(cloudpc_provider.graph_client, "graph_get_json", fake_graph_get_json)
    provider = _make_provider()

    with pytest.raises(_FakeReauthRequiredError):
        asyncio.run(provider.refresh_now(FAKE_ACCOUNT))

    # No typed result was produced -- last_result stays at its initial None.
    assert provider.last_result is None


# --- 5.5 cancellation: account switch cancels in-flight enumeration (FR-3-AC-2) -----------------


def test_cancelling_one_accounts_group_cancels_its_inflight_refresh_but_not_anothers(monkeypatch):
    account_a_started = asyncio.Event()

    async def fake_graph_get_json(url, *, auth_manager, home_account_id, scopes, max_retries=5):
        if home_account_id == "account-a":
            account_a_started.set()
            # Stays pending forever unless cancelled -- proves the cancel actually reached it.
            await asyncio.sleep(3600)
            return {"value": []}
        await asyncio.sleep(0.01)
        return {"value": []}

    monkeypatch.setattr(cloudpc_provider.graph_client, "graph_get_json", fake_graph_get_json)

    async def scenario():
        registry = TaskRegistry()
        provider = CloudPcProvider(auth_manager=DummyAuthManager(), task_registry=registry)

        group_a = registry.get_or_create_group("account-a")
        group_b = registry.get_or_create_group("account-b")

        task_a = group_a.create_task(provider.refresh_now("account-a"))
        task_b = group_b.create_task(provider.refresh_now("account-b"))

        await account_a_started.wait()
        registry.cancel_group("account-a")

        results = await asyncio.gather(task_a, task_b, return_exceptions=True)

        assert isinstance(results[0], asyncio.CancelledError)
        assert isinstance(results[1], Empty)

        # account-a's task bookkeeping is clean; account-b was entirely unaffected.
        assert registry.pending_count("account-a") == 0
        assert registry.pending_count("account-b") == 0

    asyncio.run(scenario())


def test_start_polling_schedules_the_poll_loop_inside_the_accounts_task_group(monkeypatch):
    """Confirms (by tracing, not assuming) that start_polling's loop is genuinely a task inside
    the account's task_registry group, so cancel_group alone is sufficient for FR-3-AC-2."""

    async def fake_graph_get_json(url, **kwargs):
        return {"value": []}

    monkeypatch.setattr(cloudpc_provider.graph_client, "graph_get_json", fake_graph_get_json)

    async def scenario():
        registry = TaskRegistry()
        provider = CloudPcProvider(
            auth_manager=DummyAuthManager(), task_registry=registry, poll_interval_seconds=3600.0
        )

        provider.start_polling("account-a")
        await asyncio.sleep(0)  # let the task actually get scheduled

        assert registry.pending_count("account-a") == 1

        registry.cancel_group("account-a")
        # Await the actual task (rather than a bare sleep(0)) so this assertion does not race the
        # done-callback that prunes it from the registry's bucket -- mirrors
        # tests/test_task_registry.py's own convention for this exact check.
        with pytest.raises(asyncio.CancelledError):
            await provider._poll_task

        assert registry.pending_count("account-a") == 0

    asyncio.run(scenario())


# --- pause_polling / resume_polling --------------------------------------------------------------


def test_pause_polling_stops_refreshes_and_resume_polling_continues_them(monkeypatch):
    calls: list[int] = []

    async def fake_graph_get_json(url, **kwargs):
        calls.append(1)
        return {"value": []}

    monkeypatch.setattr(cloudpc_provider.graph_client, "graph_get_json", fake_graph_get_json)

    async def scenario():
        registry = TaskRegistry()
        provider = CloudPcProvider(
            auth_manager=DummyAuthManager(), task_registry=registry, poll_interval_seconds=0.02
        )

        provider.start_polling("account-a")

        # Let at least one poll interval elapse.
        await asyncio.sleep(0.06)
        count_before_pause = len(calls)
        assert count_before_pause >= 1

        provider.pause_polling()
        await asyncio.sleep(0)  # let the cancellation actually land

        count_at_pause = len(calls)
        # Advance well past what would have been another interval if polling were still active.
        await asyncio.sleep(0.08)
        assert len(calls) == count_at_pause, "a refresh fired while polling was paused"

        provider.resume_polling("account-a")
        await asyncio.sleep(0.06)
        assert len(calls) > count_at_pause, "polling did not resume after resume_polling"

        provider.stop_polling()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_resume_polling_is_idempotent_when_already_running(monkeypatch):
    async def fake_graph_get_json(url, **kwargs):
        return {"value": []}

    monkeypatch.setattr(cloudpc_provider.graph_client, "graph_get_json", fake_graph_get_json)

    async def scenario():
        registry = TaskRegistry()
        provider = CloudPcProvider(
            auth_manager=DummyAuthManager(), task_registry=registry, poll_interval_seconds=10.0
        )

        provider.start_polling("account-a")
        await asyncio.sleep(0)
        assert registry.pending_count("account-a") == 1

        # Calling resume_polling while already running for the same account must not start a
        # second concurrent poll loop.
        provider.resume_polling("account-a")
        await asyncio.sleep(0)
        assert registry.pending_count("account-a") == 1

        running_task = provider._poll_task
        provider.stop_polling()
        # Await the actual task (rather than a bare sleep(0)) so this assertion does not race the
        # done-callback that prunes it from the registry's bucket.
        with pytest.raises(asyncio.CancelledError):
            await running_task
        assert registry.pending_count("account-a") == 0

    asyncio.run(scenario())


# --- refresh_after_action ------------------------------------------------------------------------


def test_refresh_after_action_delegates_to_refresh_now(monkeypatch):
    async def fake_graph_get_json(url, **kwargs):
        return {"value": []}

    monkeypatch.setattr(cloudpc_provider.graph_client, "graph_get_json", fake_graph_get_json)
    provider = _make_provider()

    result = asyncio.run(provider.refresh_after_action(FAKE_ACCOUNT))

    assert isinstance(result, Empty)
    assert provider.last_result is result
