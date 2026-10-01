"""Unit tests for winonlinux.asyncio_bridge's pure-asyncio behavior (fix-shutdown-loop-hygiene).

Uses the shared ``gi`` stub from tests/conftest.py: the GLib idle-pump half stays manual
(``tests/manual/app_uniqueness.md``), but ``shutdown()``'s settle/executor-join sequence and the
loop exception handler are plain asyncio and are exercised here. Each test installs the bridge
and guarantees teardown in ``finally`` so the module-global loop never leaks between tests.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

import pytest

from tests.conftest import install_gi_stub

pytestmark = pytest.mark.unit

install_gi_stub()
from winonlinux import asyncio_bridge  # noqa: E402  (stub must precede this import)


def _pump(loop: asyncio.AbstractEventLoop, iterations: int = 5) -> None:
    """Step the installed loop a few iterations, the way the GLib pump would."""
    for _ in range(iterations):
        loop.call_later(0.001, lambda: None)
        loop._run_once()  # noqa: SLF001 - mirrors the bridge's own documented use


def test_shutdown_delivers_cancellation_so_finally_runs():
    """The F-06 core: a cancelled task's `finally` executes BEFORE the loop closes -- cancel
    used to be followed immediately by loop.close(), so cleanup never ran."""
    loop = asyncio_bridge.install()
    try:
        state = {"finally_ran": False}

        async def guarded() -> None:
            try:
                await asyncio.sleep(60)
            finally:
                state["finally_ran"] = True

        task = loop.create_task(guarded(), name="guarded-task")
        _pump(loop)  # let the task start and reach its await
        task.cancel()

        asyncio_bridge.shutdown(deadline_seconds=2.0)

        assert state["finally_ran"] is True
        assert task.cancelled()
        assert not asyncio_bridge.is_installed()
    finally:
        if asyncio_bridge.is_installed():
            asyncio_bridge.uninstall()


def test_shutdown_logs_tasks_still_pending_at_the_deadline(caplog):
    """A task that ignores cancellation cannot hold shutdown past the deadline -- it is logged
    by name and the loop closes anyway."""
    loop = asyncio_bridge.install()
    try:

        async def stubborn() -> None:
            while True:
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    continue  # deliberately refuses to die

        task = loop.create_task(stubborn(), name="stubborn-task")
        _pump(loop)
        task.cancel()

        started = time.monotonic()
        with caplog.at_level(logging.WARNING):
            asyncio_bridge.shutdown(deadline_seconds=0.2)
        elapsed = time.monotonic() - started

        assert elapsed < 2.0  # bounded by the deadline, not the task's lifetime
        assert "stubborn-task" in caplog.text
        assert not asyncio_bridge.is_installed()
    finally:
        if asyncio_bridge.is_installed():
            asyncio_bridge.uninstall()


def test_shutdown_abandons_stuck_executor_worker_within_deadline(caplog):
    """The F-06 exit-lag half: a blocking call still running in the default executor bounds
    shutdown at the deadline (not the call's own duration), is logged, and runs on a daemon
    thread so it cannot hold interpreter exit either."""
    loop = asyncio_bridge.install()
    try:
        release = threading.Event()
        future = loop.run_in_executor(None, release.wait)
        _pump(loop)  # let the worker thread pick the job up

        worker_threads = [
            t for t in threading.enumerate() if t.name.startswith("winonlinux-blocking")
        ]
        assert worker_threads, "the bridge-owned executor should have spawned a worker"
        assert all(t.daemon for t in worker_threads)

        started = time.monotonic()
        with caplog.at_level(logging.WARNING):
            asyncio_bridge.shutdown(deadline_seconds=0.3)
        elapsed = time.monotonic() - started

        assert elapsed < 2.0  # the stuck worker did not stretch shutdown to its own duration
        assert "abandoning executor worker" in caplog.text

        release.set()  # unblock the abandoned worker so it exits promptly
        del future
    finally:
        if asyncio_bridge.is_installed():
            asyncio_bridge.uninstall()


def test_loop_exception_handler_is_installed_and_logs_and_continues(caplog):
    """F-24: unhandled exceptions route through the app's (redacted) logging and the loop keeps
    working afterwards."""
    loop = asyncio_bridge.install()
    try:
        assert loop.get_exception_handler() is asyncio_bridge._loop_exception_handler

        with caplog.at_level(logging.ERROR):
            loop.call_exception_handler(
                {"message": "test unhandled failure", "exception": RuntimeError("boom")}
            )

        assert "unhandled exception in asyncio" in caplog.text
        assert "test unhandled failure" in caplog.text

        # Log-and-continue: the loop still runs work after the handler fired.
        ran = {"flag": False}
        loop.call_soon(lambda: ran.__setitem__("flag", True))
        _pump(loop, iterations=2)
        assert ran["flag"] is True
    finally:
        if asyncio_bridge.is_installed():
            asyncio_bridge.uninstall()
