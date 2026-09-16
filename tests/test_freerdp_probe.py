"""Unit tests for winonlinux.freerdp_probe (spec.md: FreeRDP runtime version probe, NFR-8)."""

from __future__ import annotations

import asyncio
import subprocess
import time

import pytest

from winonlinux import freerdp_probe
from winonlinux.freerdp_probe import FreeRdpProbeResult, probe, probe_sync, reset_cache

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_probe_cache():
    """Never let one test's cached result leak into the next."""
    reset_cache()
    yield
    reset_cache()


def _fake_completed(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["xfreerdp", "/version"], returncode=0, stdout=stdout, stderr=stderr)


class _CountingRun:
    """Stand-in for subprocess.run that records how many times it was called."""

    def __init__(self, stdout: str = "", stderr: str = ""):
        self.stdout = stdout
        self.stderr = stderr
        self.call_count = 0
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.call_count += 1
        self.calls.append(list(args))
        return _fake_completed(self.stdout, self.stderr)


# -- present, above floor -----------------------------------------------------


def test_present_above_floor(monkeypatch):
    monkeypatch.setattr(freerdp_probe.shutil, "which", lambda name: "/usr/bin/xfreerdp")
    monkeypatch.setattr(
        freerdp_probe.subprocess,
        "run",
        lambda *a, **k: _fake_completed(stdout="This is FreeRDP version 3.30.0 (build abc123)\n"),
    )

    result = probe_sync()

    assert result == FreeRdpProbeResult(present=True, version="3.30.0", meets_floor=True)


def test_present_well_above_floor(monkeypatch):
    monkeypatch.setattr(freerdp_probe.shutil, "which", lambda name: "/usr/bin/xfreerdp")
    monkeypatch.setattr(
        freerdp_probe.subprocess,
        "run",
        lambda *a, **k: _fake_completed(stdout="FreeRDP 3.31.2\n"),
    )

    result = probe_sync()

    assert result.present is True
    assert result.version == "3.31.2"
    assert result.meets_floor is True


# -- present, below floor -----------------------------------------------------


def test_present_below_floor(monkeypatch):
    monkeypatch.setattr(freerdp_probe.shutil, "which", lambda name: "/usr/bin/xfreerdp")
    monkeypatch.setattr(
        freerdp_probe.subprocess,
        "run",
        lambda *a, **k: _fake_completed(stdout="FreeRDP version 2.11.5\n"),
    )

    result = probe_sync()

    assert result == FreeRdpProbeResult(present=True, version="2.11.5", meets_floor=False)


def test_present_just_below_floor_patch(monkeypatch):
    monkeypatch.setattr(freerdp_probe.shutil, "which", lambda name: "/usr/bin/xfreerdp")
    monkeypatch.setattr(
        freerdp_probe.subprocess,
        "run",
        lambda *a, **k: _fake_completed(stdout="FreeRDP 3.29.9\n"),
    )

    result = probe_sync()

    assert result.meets_floor is False


# -- absent --------------------------------------------------------------------


def test_absent(monkeypatch):
    monkeypatch.setattr(freerdp_probe.shutil, "which", lambda name: None)
    run = _CountingRun()
    monkeypatch.setattr(freerdp_probe.subprocess, "run", run)

    result = probe_sync()

    assert result == FreeRdpProbeResult(present=False, version=None, meets_floor=False)
    # Nothing to invoke a version on when the binary itself was never found.
    assert run.call_count == 0


# -- present, unparseable output -------------------------------------------------


def test_unparseable_output_is_present_but_unknown(monkeypatch):
    monkeypatch.setattr(freerdp_probe.shutil, "which", lambda name: "/usr/bin/xfreerdp")
    monkeypatch.setattr(
        freerdp_probe.subprocess,
        "run",
        lambda *a, **k: _fake_completed(stdout="", stderr="command not recognized\n"),
    )

    result = probe_sync()

    assert result.present is True
    assert result.version is None
    assert result.meets_floor is False


def test_unparseable_output_does_not_raise(monkeypatch):
    monkeypatch.setattr(freerdp_probe.shutil, "which", lambda name: "/usr/bin/xfreerdp")
    monkeypatch.setattr(
        freerdp_probe.subprocess,
        "run",
        lambda *a, **k: _fake_completed(stdout="garbage with no version-shaped text at all"),
    )

    # Must not raise.
    result = probe_sync()

    assert result.meets_floor is False


def test_subprocess_spawn_failure_is_present_but_unknown(monkeypatch):
    """A binary that shutil.which found but that fails to actually run (e.g. permissions,
    race with removal) must still resolve to present-but-unknown, never an exception."""

    monkeypatch.setattr(freerdp_probe.shutil, "which", lambda name: "/usr/bin/xfreerdp")

    def _raise(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(freerdp_probe.subprocess, "run", _raise)

    result = probe_sync()

    assert result.present is True
    assert result.version is None
    assert result.meets_floor is False


# -- caching ---------------------------------------------------------------------


def test_probe_caches_and_does_not_reinvoke_subprocess(monkeypatch):
    monkeypatch.setattr(freerdp_probe.shutil, "which", lambda name: "/usr/bin/xfreerdp")
    run = _CountingRun(stdout="FreeRDP 3.30.0\n")
    monkeypatch.setattr(freerdp_probe.subprocess, "run", run)

    first = asyncio.run(probe())
    second = asyncio.run(probe())

    assert first == second == FreeRdpProbeResult(present=True, version="3.30.0", meets_floor=True)
    assert run.call_count == 1


def test_reset_cache_forces_reprobe(monkeypatch):
    monkeypatch.setattr(freerdp_probe.shutil, "which", lambda name: "/usr/bin/xfreerdp")
    run = _CountingRun(stdout="FreeRDP 3.30.0\n")
    monkeypatch.setattr(freerdp_probe.subprocess, "run", run)

    asyncio.run(probe())
    reset_cache()
    asyncio.run(probe())

    assert run.call_count == 2


def test_concurrent_probe_calls_share_one_subprocess_invocation(monkeypatch):
    # Two callers that both start before either has finished must share the same in-flight
    # probe rather than each independently spawning xfreerdp -- probe()'s own docstring promises
    # this, and it is what _probe_lock exists to guarantee.
    monkeypatch.setattr(freerdp_probe.shutil, "which", lambda name: "/usr/bin/xfreerdp")

    class _SlowCountingRun(_CountingRun):
        """Like _CountingRun, but slow enough that a second concurrent caller genuinely starts
        (and must wait on _probe_lock) before the first invocation returns."""

        def __call__(self, args, **kwargs):
            time.sleep(0.05)
            return super().__call__(args, **kwargs)

    run = _SlowCountingRun(stdout="FreeRDP 3.30.0\n")
    monkeypatch.setattr(freerdp_probe.subprocess, "run", run)

    async def run_two_concurrent_probes():
        return await asyncio.gather(probe(), probe())

    first, second = asyncio.run(run_two_concurrent_probes())

    assert first == second == FreeRdpProbeResult(present=True, version="3.30.0", meets_floor=True)
    assert run.call_count == 1


def test_probe_dispatches_through_the_running_loops_executor(monkeypatch):
    """probe() must hand the sync work to loop.run_in_executor rather than call probe_sync()
    inline on the calling coroutine -- otherwise a slow/hanging xfreerdp would block the loop.

    This only fakes the *current* running loop's ``run_in_executor`` (via
    ``loop.set_default_executor``-independent monkeypatching of the loop instance asyncio.run
    hands to the coroutine), not any asyncio module-level function, so it can't disturb
    asyncio.run's own machinery.
    """
    monkeypatch.setattr(freerdp_probe.shutil, "which", lambda name: "/usr/bin/xfreerdp")
    monkeypatch.setattr(
        freerdp_probe.subprocess,
        "run",
        lambda *a, **k: _fake_completed(stdout="FreeRDP 3.30.0\n"),
    )

    calls: list[str] = []

    async def run_and_record():
        loop = asyncio.get_running_loop()
        real_run_in_executor = loop.run_in_executor

        def spying_run_in_executor(executor, func, *args):
            calls.append("executor")
            return real_run_in_executor(executor, func, *args)

        monkeypatch.setattr(loop, "run_in_executor", spying_run_in_executor)
        return await probe()

    result = asyncio.run(run_and_record())

    assert calls == ["executor"]
    assert result.meets_floor is True
