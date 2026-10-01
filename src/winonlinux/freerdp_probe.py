"""FreeRDP runtime version probe.

Implements the "FreeRDP runtime version probe" requirement (spec.md, add-app-foundation change,
NFR-8, §9):

- Detects at runtime whether ``xfreerdp`` is present on ``PATH`` and, if so, what version it
  reports, so the native launch path can be disabled with a specific reason (absent / below the
  version floor / present-but-unknown) instead of attempted blind.
- The floor is 3.30.0. Versions are compared as ``(major, minor, patch)`` tuples.
- The subprocess spawn never runs synchronously on the event loop: :func:`probe` is the async
  entry point and hands the actual spawn to an executor via
  ``loop.run_in_executor``. The spawn itself lives in the plain, synchronous
  :func:`probe_sync` (and, below that, :func:`_invoke_version`), so the probing logic is directly
  unit-testable without asyncio at all.
- The result is cached for the app session on first call; :func:`reset_cache` clears the cache
  (test-only -- production code is expected to probe once per session).

ASSUMED, not yet verified (spec.md §12 risk 14): the exact shape of ``xfreerdp /version`` output.
This has not been checked against a real FreeRDP 3.30 build. Parsing below is deliberately
defensive -- it looks for an ``X.Y.Z`` version number pattern anywhere in the combined
stdout/stderr text rather than assuming a fixed line format or flag. If the real output does not
contain a bare ``X.Y.Z`` (e.g. it's wrapped in surrounding text like "FreeRDP version
3.30.0-dev"), the pattern still matches; if the flag itself (``/version``) turns out to be wrong
for some build, that build is reported as present-but-unknown rather than crashing. This needs
checking against a real 3.30 build (see design.md's Stage 3 manual verification plan).

This module only ever imports the standard library directly, matching state_store.py's
no-ordering-dependency convention with the parallel logging_setup task.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import shutil
import subprocess

__all__ = [
    "FreeRdpProbeResult",
    "FREERDP_VERSION_FLOOR",
    "probe",
    "probe_sync",
    "reset_cache",
]

logger = logging.getLogger(__name__)

#: Minimum FreeRDP version the native launch path requires (NFR-8). Compared as a
#: ``(major, minor, patch)`` tuple against the parsed version.
FREERDP_VERSION_FLOOR: tuple[int, int, int] = (3, 30, 0)

#: Matches the first ``X.Y.Z`` numeric version anywhere in the probe output. Deliberately loose
#: (see module docstring's ASSUMED note) -- it does not anchor to a specific line or prefix.
_VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)")

#: Upper bound on how long the version invocation is allowed to run before it's treated as a
#: failure. ``xfreerdp /version`` is expected to return near-instantly; this only guards against
#: a hang.
_VERSION_PROBE_TIMEOUT_SECONDS = 5.0


@dataclasses.dataclass(frozen=True)
class FreeRdpProbeResult:
    """Immutable outcome of probing for ``xfreerdp``.

    Attributes
    ----------
    present:
        Whether an ``xfreerdp`` binary was found on ``PATH`` at all.
    version:
        The parsed ``X.Y.Z`` version string, or ``None`` when ``xfreerdp`` is absent, or when it
        is present but its version output did not parse (see module docstring). ``None`` is used
        rather than the raw unparsed text so consumers never have to sniff whether ``version``
        holds a real version -- ``version is not None`` alone implies a successfully parsed one.
    meets_floor:
        ``True`` only when ``version`` is known and ``>= FREERDP_VERSION_FLOOR``. Always ``False``
        when ``version`` is ``None`` (absent, or present-but-unknown).
    """

    present: bool
    version: str | None
    meets_floor: bool


#: Module-level cache populated by the first call to :func:`probe` in an app session.
#: ``None`` means "not probed yet"; :func:`reset_cache` (test-only) sets it back to ``None``.
_cached_result: FreeRdpProbeResult | None = None

#: Serializes concurrent :func:`probe` callers so at most one actually spawns the subprocess;
#: everyone else awaits the same in-flight call instead of redundantly invoking ``xfreerdp``.
#: Created lazily (see :func:`probe`) rather than at import time, since binding an ``asyncio.Lock``
#: makes the most sense once a running loop actually exists.
_probe_lock: asyncio.Lock | None = None


def reset_cache() -> None:
    """Clear the cached probe result.

    Test-only. Production code calls :func:`probe` once per app session and relies on the cache
    thereafter; tests use this to force a fresh probe between cases. Also drops the in-flight
    lock so each test case starts from the same clean state as the others.
    """
    global _cached_result, _probe_lock
    _cached_result = None
    _probe_lock = None


def _locate_xfreerdp() -> str | None:
    """Return the path to the ``xfreerdp`` binary on ``PATH``, or ``None`` if none is found."""
    return shutil.which("xfreerdp")


def _invoke_version(binary_path: str) -> str:
    """Spawn ``<binary_path> /version`` and return its combined stdout+stderr text.

    This is the one place that actually spawns a subprocess. It is a plain synchronous function
    with no asyncio involvement, so it (and everything built on it, including
    :func:`probe_sync`) is directly unit-testable by monkeypatching ``subprocess.run`` --
    :func:`probe`, the async entry point, is the only caller that must not run this on the event
    loop thread directly; it dispatches to an executor instead.
    """
    completed = subprocess.run(
        [binary_path, "/version"],
        capture_output=True,
        text=True,
        timeout=_VERSION_PROBE_TIMEOUT_SECONDS,
        check=False,
    )
    return f"{completed.stdout}\n{completed.stderr}"


def _parse_version(output: str) -> str | None:
    """Extract an ``X.Y.Z`` version string from probe output, or ``None`` if none is found."""
    match = _VERSION_PATTERN.search(output)
    if match is None:
        return None
    return match.group(0)


def _meets_floor(version: str) -> bool:
    """Whether ``version`` (an ``X.Y.Z`` string) is ``>= FREERDP_VERSION_FLOOR``."""
    try:
        numeric = tuple(int(part) for part in version.split(".")[:3])
    except ValueError:
        return False
    if len(numeric) != 3:
        return False
    return numeric >= FREERDP_VERSION_FLOOR


def probe_sync() -> FreeRdpProbeResult:
    """Run the full, uncached probe synchronously: locate, invoke, parse, compare.

    Never raises -- every failure mode (binary absent, spawn failure, unparseable output) maps to
    a :class:`FreeRdpProbeResult` field combination instead of an exception, per NFR-8's
    disable-with-reason contract.

    This does not consult or populate the session cache; :func:`probe` (the async entry point)
    owns caching. Call this directly only from tests or other strictly-synchronous, off-loop
    contexts -- an event-loop caller MUST use :func:`probe` instead, since this spawns a
    subprocess synchronously.
    """
    binary_path = _locate_xfreerdp()
    if binary_path is None:
        return FreeRdpProbeResult(present=False, version=None, meets_floor=False)

    try:
        output = _invoke_version(binary_path)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("xfreerdp version probe failed to run %s: %s", binary_path, exc)
        return FreeRdpProbeResult(present=True, version=None, meets_floor=False)

    version = _parse_version(output)
    if version is None:
        logger.warning(
            "xfreerdp found at %s but its /version output did not contain a recognizable "
            "X.Y.Z version number; treating as present but unknown",
            binary_path,
        )
        return FreeRdpProbeResult(present=True, version=None, meets_floor=False)

    return FreeRdpProbeResult(
        present=True,
        version=version,
        meets_floor=_meets_floor(version),
    )


async def probe() -> FreeRdpProbeResult:
    """Probe for ``xfreerdp`` and its version, off the event loop thread, caching per session.

    The subprocess spawn happens in an executor (``loop.run_in_executor``), never synchronously
    on the calling coroutine's loop. Safe to call from multiple places: only the first call in
    the app session actually probes; every later call -- including concurrent ones started before
    the first completes, which share the in-flight call via :data:`_probe_lock` rather than each
    spawning their own ``xfreerdp`` subprocess -- returns the cached :class:`FreeRdpProbeResult`.
    """
    global _cached_result, _probe_lock
    if _cached_result is not None:
        return _cached_result

    if _probe_lock is None:
        _probe_lock = asyncio.Lock()

    async with _probe_lock:
        # Re-check: a concurrent caller may have finished the probe while we were waiting for
        # the lock, in which case we must return its result rather than probing a second time.
        if _cached_result is not None:
            return _cached_result

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, probe_sync)
        _cached_result = result
        return result
