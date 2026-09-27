"""GLib <-> asyncio integration: one thread, one event loop (D-18).

Implements the event-loop half of the "Single asyncio event loop with per-account task groups"
requirement (spec.md, add-app-foundation change). GTK4/libadwaita's main loop and Python's
``asyncio`` loop are made to share one OS thread so that:

- Every UI update and every asyncio callback runs on the same thread, so callers never need
  ``GLib.idle_add``/``call_soon_threadsafe`` marshalling between the two, and the exact races
  D-18 exists to avoid (a UI update racing an async callback across threads) cannot occur.
- Blocking work (keyring, subprocess) is offloaded to :func:`winonlinux.task_registry.run_blocking`
  instead, which runs it in a thread executor -- keeping this single thread free to keep pumping
  both loops.

Chosen approach: a **GLib idle-source pump** that steps the asyncio loop in short, non-blocking
bursts from inside GTK's own main loop, rather than a separate library that hands GLib control of
the *whole* loop (e.g. a ``gbulb``-style custom asyncio event loop policy backed by GLib's C main
loop). The pump approach was chosen because it needs nothing beyond PyGObject and the standard
library: `install()` schedules a recurring ``GLib.idle_add`` callback that calls the private-but-
stable ``BaseEventLoop._run_once()`` on a plain ``asyncio.new_event_loop()`` once per GTK
iteration, so asyncio callbacks, timers, and I/O readiness are all serviced promptly between GTK
events.

``_run_once()`` itself is only non-blocking when it has something to do (a ready callback or a
due timer) -- with neither, it computes an internal ``select()`` timeout of ``None`` and blocks
the *entire* thread, GTK dispatch included, until an event loop file descriptor becomes ready.
Since this thread also runs GTK's own dispatch, an unbounded block there would freeze the whole
UI for however long nothing asyncio-visible happens (e.g. a keyring or subprocess call awaited
via ``run_in_executor`` that hasn't finished yet). :func:`install` guards against this by keeping
a trivial, ever-rescheduling ``call_later`` heartbeat on the loop (see ``_PUMP_HEARTBEAT_SECONDS``
below): as long as *something* is always scheduled a few milliseconds out, ``_run_once()``'s
internal timeout is always that small bounded value instead of ``None``, so a single pump call can
never withhold the thread from GTK for longer than the heartbeat interval, even when asyncio truly
has nothing else to do.

This choice is deliberately isolated behind this module's four functions (``install``,
``uninstall``, ``get_loop``, ``is_installed``) exactly as design.md's "Open Questions" flags: if a
GLib-native asyncio policy (e.g. a maintained ``gbulb`` successor) becomes preferable later, only
this file needs to change -- every other module talks to asyncio via the standard ``asyncio.*``
API and never imports GLib for scheduling.

This is the one module in the app-foundation change that imports ``gi``/GLib. The GLib-pump half
cannot be exercised without PyGObject (``tests/manual/app_uniqueness.md`` documents that manual
verification); the pure-asyncio halves added by fix-shutdown-loop-hygiene -- ``shutdown()``'s
settle/executor-join sequence and the loop exception handler -- ARE covered by automated tests
(``tests/test_asyncio_bridge.py``) under a minimal ``gi`` stub.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import concurrent.futures.thread
import logging
import threading
import time
import weakref

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402  (require_version must precede this import)

__all__ = [
    "install",
    "uninstall",
    "shutdown",
    "get_loop",
    "is_installed",
]

logger = logging.getLogger(__name__)

#: Idle-pump interval isn't fixed -- GLib.idle_add runs the callback whenever GTK's main loop is
#: otherwise idle, which is exactly "as often as possible without starving GTK's own event
#: dispatch". No timeout/priority tuning has been needed so far; if idle-priority pumping ever
#: shows up as a startup-latency contributor under the NFR-1 measurement (V3), tune
#: ``GLib.PRIORITY_*`` here -- this comment is the flagged spot per the module docstring.
_PRIORITY = GLib.PRIORITY_DEFAULT

#: Upper bound, in seconds, on how long a single ``_run_once()`` call can block the thread (and
#: therefore GTK's own dispatch) while asyncio has no ready callback or due timer of its own. Kept
#: short enough to be imperceptible as UI latency, long enough not to burn CPU rescheduling itself.
_PUMP_HEARTBEAT_SECONDS = 0.02

#: Overall bound on :func:`shutdown`: cancelled tasks get this long to run their cleanup, and the
#: executor gets whatever remains of it to join -- generous for `finally` blocks that only close
#: sockets/files, small enough that quitting never feels hung (fix-shutdown-loop-hygiene, F-06).
_SHUTDOWN_DEADLINE_SECONDS = 2.0

#: Per-iteration timer scheduled while settling so `_run_once()` never blocks the settle loop for
#: longer than this between checks.
_SETTLE_TICK_SECONDS = 0.01

_loop: asyncio.AbstractEventLoop | None = None
_idle_source_id: int | None = None
_executor: "concurrent.futures.ThreadPoolExecutor | None" = None


class _DaemonThreadPoolExecutor(concurrent.futures.ThreadPoolExecutor):
    """A ``ThreadPoolExecutor`` whose workers are daemon threads that the interpreter neither
    joins at exit nor waits for.

    This is the loop's default executor (every ``run_blocking`` call lands here). Stock
    ``ThreadPoolExecutor`` workers are non-daemon since CPython 3.9, and ``threading._shutdown``
    joins every non-daemon thread at interpreter exit -- so one in-flight blocking call (e.g. a
    ``requests.get`` mid-timeout) holds the whole process open after the user quits, which is
    exactly audit finding F-06's exit-lag half. Daemon workers make "abandon with a WARNING"
    (this change's spec) real instead of aspirational.

    ASSUMED-stable internals, same posture as this module's ``_run_once()`` use: the override
    below copies ``ThreadPoolExecutor._adjust_thread_count`` (stable across CPython 3.9-3.13)
    with two deliberate differences -- ``daemon=True``, and no ``_threads_queues`` registration
    (that registry exists so ``concurrent.futures``' atexit hook can join workers; daemon workers
    must be exempt from exactly that join). Verified continuously once add-audit-test-coverage's
    CI version matrix lands (BIG-342).
    """

    def _adjust_thread_count(self) -> None:
        # Copied from CPython's concurrent/futures/thread.py (see class docstring).
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_cb(_, q=self._work_queue):  # pragma: no cover - GC-timing dependent
            q.put(None)

        num_threads = len(self._threads)
        if num_threads < self._max_workers:
            thread_name = f"{self._thread_name_prefix or self}_{num_threads}"
            t = threading.Thread(
                name=thread_name,
                target=concurrent.futures.thread._worker,  # noqa: SLF001 - see class docstring
                args=(
                    weakref.ref(self, weakref_cb),
                    self._work_queue,
                    self._initializer,
                    self._initargs,
                ),
                daemon=True,
            )
            t.start()
            self._threads.add(t)


def _loop_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Route unhandled task/callback exceptions through the app's (redacted) logging.

    Installed on the loop by :func:`install` (fix-shutdown-loop-hygiene, audit F-24) so nothing
    falls to asyncio's default stderr handler at GC time, outside §10.7's record-factory
    discipline. Log-and-continue, never terminate: a background task dying must not take the
    application down -- user-facing surfacing of failures travels through the state/result
    listeners, not through this handler.
    """
    exception = context.get("exception")
    source = context.get("task") or context.get("future") or context.get("handle")
    source_name = getattr(source, "get_name", lambda: None)() or repr(source)
    logger.error(
        "asyncio_bridge: unhandled exception in asyncio (%s; source=%s) -- logged and continuing",
        context.get("message") or "no message supplied",
        source_name,
        exc_info=exception,
    )


def _reschedule_heartbeat(loop: asyncio.AbstractEventLoop) -> None:
    """Keep something always scheduled so `_run_once()` never computes an unbounded timeout.

    A pure no-op callback that reschedules itself every :data:`_PUMP_HEARTBEAT_SECONDS`. Its only
    purpose is to guarantee ``loop._scheduled`` is never empty, which bounds the internal
    ``select()`` timeout `_pump()` below relies on -- see the module docstring.
    """
    loop.call_later(_PUMP_HEARTBEAT_SECONDS, _reschedule_heartbeat, loop)


def install() -> asyncio.AbstractEventLoop:
    """Create the process's one asyncio loop and start pumping it from GTK's main loop.

    Idempotent: calling this again while already installed just returns the existing loop and
    does not add a second pump source. Must be called from the thread that will also run
    ``Gtk.Application.run()`` / ``GLib.MainLoop.run()`` -- both loops share that one thread.
    """
    global _loop, _idle_source_id

    if _loop is not None:
        return _loop

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _reschedule_heartbeat(loop)

    # Unhandled task exceptions land in redacted logs, not stderr-at-GC-time (F-24).
    loop.set_exception_handler(_loop_exception_handler)

    # The bridge owns the default executor so shutdown() can tear it down within a bound and a
    # stuck worker cannot hold interpreter exit -- see _DaemonThreadPoolExecutor (F-06).
    global _executor
    _executor = _DaemonThreadPoolExecutor(thread_name_prefix="winonlinux-blocking")
    loop.set_default_executor(_executor)

    # asyncio.get_running_loop() -- which run_blocking(), asyncio.ensure_future(), and virtually
    # all real asyncio code depends on -- only succeeds while a loop is marked "running" via
    # asyncio.events._set_running_loop(). Normally that happens automatically inside
    # loop.run_forever(); since this module deliberately never calls run_forever() (it steps the
    # loop in short bursts instead, see _pump() below), nothing else would ever set that flag.
    # Set it once, for the process lifetime of the installed loop, so every asyncio callback and
    # task this loop drives sees a running loop exactly as if run_forever() were active.
    asyncio.events._set_running_loop(loop)  # noqa: SLF001 -- see comment above

    def _pump() -> bool:
        # BaseEventLoop._run_once() runs exactly one iteration: it services ready callbacks,
        # due timers, and any I/O that's ready, then returns. Left to itself it can block this
        # thread -- GTK's own dispatch included -- for as long as nothing asyncio-visible happens,
        # because its internal select() timeout is unbounded when there is no ready callback and
        # no due timer; the heartbeat scheduled by install() (see _reschedule_heartbeat and the
        # module docstring) keeps a timer always due within _PUMP_HEARTBEAT_SECONDS, which bounds
        # that timeout and is what actually makes this safe to call from a GLib idle callback.
        # This relies on the standard library's asyncio internals (an underscore-prefixed method,
        # hence "private-but-stable" above) rather than a public API, because asyncio has no
        # public "run exactly one non-blocking iteration" entry point; CPython has kept this
        # method's behavior stable across 3.x releases.
        loop._run_once()  # noqa: SLF001 -- see comment above
        if loop.is_closed():
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    _idle_source_id = GLib.idle_add(_pump, priority=_PRIORITY)
    _loop = loop
    logger.debug("asyncio_bridge: installed GLib idle pump for the asyncio loop")
    return loop


def uninstall() -> None:
    """Stop pumping and close the installed loop. Safe to call when not installed (no-op).

    Bare teardown only: pending tasks are NOT settled and the executor is NOT drained here --
    :func:`shutdown` is the orderly variant application exit uses (F-06); this remains for
    callers that need an immediate, unconditional teardown.
    """
    global _loop, _idle_source_id, _executor

    if _idle_source_id is not None:
        GLib.source_remove(_idle_source_id)
        _idle_source_id = None

    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None

    if _loop is not None:
        asyncio.events._set_running_loop(None)  # noqa: SLF001 -- undo the install()-time set
        if not _loop.is_closed():
            _loop.close()
        _loop = None
        logger.debug("asyncio_bridge: uninstalled the asyncio loop")


def shutdown(deadline_seconds: float = _SHUTDOWN_DEADLINE_SECONDS) -> None:
    """Orderly teardown, bounded by ``deadline_seconds`` overall (F-06).

    Sequence: pump the loop until every pending task has settled (delivering the
    ``CancelledError`` the caller's ``destroy_group`` calls already requested, so ``finally``
    blocks actually run) or the deadline elapses -- logging any task still pending by name --
    then join the executor within whatever remains of the deadline, abandoning (and naming) any
    still-running daemon worker, then :func:`uninstall`. Safe to call when not installed.
    """
    started = time.monotonic()

    if _loop is not None and not _loop.is_closed():
        leftover = _settle_pending_tasks(_loop, deadline_seconds)
        for task in leftover:
            logger.warning(
                "asyncio_bridge: task %r still pending at the %.1fs shutdown deadline; closing "
                "the loop without its cleanup having run",
                task.get_name(),
                deadline_seconds,
            )

    remaining = max(0.0, deadline_seconds - (time.monotonic() - started))
    _join_executor_bounded(remaining)

    uninstall()


def _settle_pending_tasks(
    loop: asyncio.AbstractEventLoop, deadline_seconds: float
) -> "list[asyncio.Task]":
    """Pump ``loop`` until it has no pending tasks or the deadline passes; return the leftovers.

    Each iteration schedules a short no-op timer first so ``_run_once()``'s internal ``select()``
    timeout is bounded by :data:`_SETTLE_TICK_SECONDS` even when the GLib-side pump (and its
    heartbeat's rescheduling) is no longer running.
    """
    deadline = time.monotonic() + deadline_seconds
    while True:
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        if not pending:
            return []
        if time.monotonic() >= deadline:
            return pending
        loop.call_later(_SETTLE_TICK_SECONDS, _noop)
        loop._run_once()  # noqa: SLF001 -- same private-but-stable use as _pump(), see install()


def _noop() -> None:
    pass


def _join_executor_bounded(deadline_seconds: float) -> None:
    """Join the bridge-owned executor within ``deadline_seconds``; abandon-with-WARNING after.

    The join itself runs on a helper daemon thread so a stuck worker (e.g. a blocking HTTP call
    mid-timeout) bounds this function at the deadline instead of the worker's own duration; an
    abandoned worker is a daemon thread (see :class:`_DaemonThreadPoolExecutor`) and therefore
    cannot hold interpreter exit either.
    """
    global _executor
    executor = _executor
    _executor = None
    if executor is None:
        return

    executor.shutdown(wait=False, cancel_futures=True)
    joiner = threading.Thread(
        target=executor.shutdown, kwargs={"wait": True}, daemon=True, name="winonlinux-exec-join"
    )
    joiner.start()
    joiner.join(deadline_seconds)
    if joiner.is_alive():
        alive = [t.name for t in getattr(executor, "_threads", ()) if t.is_alive()]
        logger.warning(
            "asyncio_bridge: abandoning executor worker(s) still running at shutdown "
            "(daemon threads; they cannot hold process exit): %s",
            ", ".join(alive) or "<unknown>",
        )


def get_loop() -> asyncio.AbstractEventLoop:
    """Return the installed loop. Raises ``RuntimeError`` if :func:`install` has not run yet."""
    if _loop is None:
        raise RuntimeError(
            "asyncio_bridge.install() has not been called yet -- there is no loop to return"
        )
    return _loop


def is_installed() -> bool:
    """Whether :func:`install` has run and not since been undone by :func:`uninstall`."""
    return _loop is not None
