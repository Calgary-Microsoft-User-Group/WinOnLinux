"""Unit tests for winonlinux.task_registry (spec.md: per-account task groups, D-18).

Pure asyncio -- no ``gi``/GTK import anywhere in this module or in
``winonlinux.task_registry`` itself, so this file runs in any environment with plain CPython and
pytest, no PyGObject required. Tests drive their own event loop with ``asyncio.run`` directly
(matching ``tests/test_freerdp_probe.py``'s convention) rather than depending on a pytest-asyncio
plugin.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from winonlinux.task_registry import APP_SCOPE, TaskRegistry, run_blocking

pytestmark = pytest.mark.unit


# -- cancellation isolation ----------------------------------------------------


def test_cancelling_one_account_group_leaves_other_accounts_tasks_running():
    """spec.md scenario "Account task group cancellation is isolated"."""

    async def scenario():
        registry = TaskRegistry()
        group_a = registry.get_or_create_group("account-a")
        group_b = registry.get_or_create_group("account-b")

        never_set = asyncio.Event()

        async def a_work():
            # Stays pending forever unless cancelled -- exactly the shape needed to prove a
            # cancel actually reached it.
            await never_set.wait()

        b_completed_flag = {"done": False}

        async def b_work():
            await asyncio.sleep(0.05)
            b_completed_flag["done"] = True
            return "b-result"

        task_a = group_a.create_task(a_work())
        task_b = group_b.create_task(b_work())

        # Let both tasks actually start running before cancelling.
        await asyncio.sleep(0)
        assert registry.pending_count("account-a") == 1
        assert registry.pending_count("account-b") == 1

        registry.cancel_group("account-a")

        results = await asyncio.gather(task_a, task_b, return_exceptions=True)

        assert isinstance(results[0], asyncio.CancelledError)
        assert results[1] == "b-result"
        assert b_completed_flag["done"] is True

        # Bookkeeping for both accounts is clean afterward: cancel_group() does not destroy
        # account-a's registration, just its tasks.
        assert registry.pending_count("account-a") == 0
        assert registry.pending_count("account-b") == 0
        assert "account-a" in registry.active_account_ids()
        assert "account-b" in registry.active_account_ids()

    asyncio.run(scenario())


def test_cancel_group_on_one_account_never_touches_app_scope():
    async def scenario():
        registry = TaskRegistry()
        account_group = registry.get_or_create_group("account-a")
        app_group = registry.get_or_create_group()  # defaults to APP_SCOPE

        app_completed_flag = {"done": False}

        async def app_work():
            await asyncio.sleep(0.02)
            app_completed_flag["done"] = True
            return "app-result"

        never_set = asyncio.Event()
        account_task = account_group.create_task(never_set.wait())
        app_task = app_group.create_task(app_work())

        await asyncio.sleep(0)
        registry.cancel_group("account-a")

        results = await asyncio.gather(account_task, app_task, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        assert results[1] == "app-result"
        assert app_completed_flag["done"] is True

    asyncio.run(scenario())


def test_none_and_app_scope_sentinel_resolve_to_the_same_group():
    async def scenario():
        registry = TaskRegistry()
        via_none = registry.get_or_create_group(None)
        via_sentinel = registry.get_or_create_group(APP_SCOPE)

        never_set = asyncio.Event()
        task = via_none.create_task(never_set.wait())
        await asyncio.sleep(0)

        # A task created through the None-spelled handle is visible/cancellable through the
        # APP_SCOPE-spelled handle -- they're the same underlying group.
        assert registry.pending_count(APP_SCOPE) == 1
        registry.cancel_group(APP_SCOPE)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert via_sentinel.tasks == frozenset()

    asyncio.run(scenario())


# -- run_blocking / executor offload -------------------------------------------


def _blocking_sleep(seconds: float) -> str:
    time.sleep(seconds)
    return "blocked-result"


def test_run_blocking_offloads_to_executor_without_stalling_the_loop():
    """spec.md scenario "Blocking call does not stall the loop".

    A 100ms ``time.sleep`` dispatched through ``run_blocking`` must not prevent a concurrently
    running coroutine from making rapid progress via zero-delay ``asyncio.sleep(0)`` yields --
    proving the blocking call ran off the loop thread rather than inline.
    """

    async def scenario():
        blocking_task = asyncio.ensure_future(run_blocking(_blocking_sleep, 0.1))

        yields = []
        started = time.perf_counter()
        for i in range(20):
            await asyncio.sleep(0)
            yields.append(i)
        yield_elapsed = time.perf_counter() - started

        assert len(yields) == 20
        # 20 zero-delay yields complete near-instantly if (and only if) the loop was free to
        # keep running them while the 100ms blocking call was still in flight in its executor
        # thread; comfortably under the 100ms the blocking call itself takes.
        assert yield_elapsed < 0.08

        result = await blocking_task
        assert result == "blocked-result"

    asyncio.run(scenario())


def test_run_blocking_passes_positional_and_keyword_arguments():
    def combine(a, b, *, sep):
        return f"{a}{sep}{b}"

    async def scenario():
        return await run_blocking(combine, "x", "y", sep="-")

    assert asyncio.run(scenario()) == "x-y"


# -- destroy_group bookkeeping --------------------------------------------------


def test_destroy_group_removes_bookkeeping_for_fresh_reuse():
    async def scenario():
        registry = TaskRegistry()
        group = registry.get_or_create_group("account-a")

        never_set = asyncio.Event()
        task = group.create_task(never_set.wait())
        await asyncio.sleep(0)
        assert registry.pending_count("account-a") == 1

        registry.destroy_group("account-a")
        # Give the cancellation a chance to actually be delivered.
        await asyncio.sleep(0)

        assert task.cancelled()
        assert "account-a" not in registry.active_account_ids()
        assert registry.pending_count("account-a") == 0

        # A fresh handle for the same account ID starts genuinely empty, not reusing the
        # destroyed group's (already-cancelled) task state.
        fresh = registry.get_or_create_group("account-a")
        assert fresh.tasks == frozenset()
        assert registry.pending_count("account-a") == 0

        # And it's independently usable: a new task can be created and tracked under it.
        completed_flag = {"done": False}

        async def fresh_work():
            completed_flag["done"] = True
            return "fresh-result"

        fresh_task = fresh.create_task(fresh_work())
        assert await fresh_task == "fresh-result"
        assert completed_flag["done"] is True

    asyncio.run(scenario())


def test_handle_destroy_matches_registry_destroy_group():
    async def scenario():
        registry = TaskRegistry()
        group = registry.get_or_create_group("account-a")
        never_set = asyncio.Event()
        group.create_task(never_set.wait())
        await asyncio.sleep(0)

        group.destroy()
        await asyncio.sleep(0)

        assert "account-a" not in registry.active_account_ids()

    asyncio.run(scenario())


def test_finished_tasks_are_pruned_from_pending_count_automatically():
    async def scenario():
        registry = TaskRegistry()
        group = registry.get_or_create_group("account-a")

        async def quick():
            return "done"

        task = group.create_task(quick())
        result = await task
        assert result == "done"
        # The done-callback prunes finished tasks from the tracked set on its own; no explicit
        # cancel/destroy needed for a task that simply finished.
        assert registry.pending_count("account-a") == 0

    asyncio.run(scenario())
