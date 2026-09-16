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

This is the one module in the app-foundation change that imports ``gi``/GLib; it cannot be
exercised in an environment without PyGObject installed (see the design.md non-goal on UI content
and the task instructions for this change: no automated test targets this file, only
``tests/manual/app_uniqueness.md`` documents the manual verification the running application
needs).
"""

from __future__ import annotations

import asyncio
import logging

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402  (require_version must precede this import)

__all__ = [
    "install",
    "uninstall",
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

_loop: asyncio.AbstractEventLoop | None = None
_idle_source_id: int | None = None


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
    """Stop pumping and close the installed loop. Safe to call when not installed (no-op)."""
    global _loop, _idle_source_id

    if _idle_source_id is not None:
        GLib.source_remove(_idle_source_id)
        _idle_source_id = None

    if _loop is not None:
        asyncio.events._set_running_loop(None)  # noqa: SLF001 -- undo the install()-time set
        if not _loop.is_closed():
            _loop.close()
        _loop = None
        logger.debug("asyncio_bridge: uninstalled the asyncio loop")


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
