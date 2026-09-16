"""Application shell: single-instance ``Adw.Application`` entry point.

Implements the "Single application instance" requirement (spec.md, add-app-foundation change,
FR-4-AC-7, §6.3) and wires in the other pieces this change adds: the GLib<->asyncio bridge
(``winonlinux.asyncio_bridge``, D-18), the per-account task-group registry
(``winonlinux.task_registry``), the FreeRDP version probe (``winonlinux.freerdp_probe``, NFR-8),
and the non-secret state store (``winonlinux.state_store``, D-13).

Single-instance behavior comes for free from ``Gio.Application``/``Adw.Application``: an
application ID plus the *default* ``Gio.ApplicationFlags`` (i.e. NOT
``Gio.ApplicationFlags.NON_UNIQUE``) makes the first ``run()`` claim a well-known bus name; every
later ``run()`` in a second process detects the name is already owned, forwards its activation
request to the first process over D-Bus, and returns immediately without ever locally emitting
"activate" -- so the second process's own code never runs past ``app.run()``. This is why
:func:`main` is structured the way it is: nothing that constructs a token cache, a state store, or
any other stateful/side-effecting object happens before ``app.run()`` is called. Everything that
must exist only once per *running application instance* (the task registry, the asyncio bridge,
the main window) is built inside the "activate" handler instead, which for a second launch simply
never fires locally.

No real UI content lives here -- a bare ``Adw.ApplicationWindow`` placeholder is standing in until
the UI-shell change adds actual content; that is intentionally out of scope for this change (see
design.md's Non-Goals).
"""

from __future__ import annotations

import logging
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402  (require_version must precede this import)

from winonlinux import asyncio_bridge
from winonlinux.freerdp_probe import FreeRdpProbeResult
from winonlinux.freerdp_probe import probe as probe_freerdp
from winonlinux.state_store import StateStore
from winonlinux.task_registry import TaskRegistry

__all__ = [
    "APPLICATION_ID",
    "WinOnLinuxApplication",
    "main",
]

logger = logging.getLogger(__name__)

#: Stable application ID used as both the ``GApplication``/D-Bus well-known name and (per
#: add-flatpak-packaging, which consumes this same constant) the Flatpak app ID. Recorded as
#: spec.md decision D-21: provisional, owned by this change. Do not invent a different ID
#: elsewhere -- import this constant instead.
APPLICATION_ID = "io.github.CalgaryMicrosoftUserGroup.WinOnLinux"


class WinOnLinuxApplication(Adw.Application):
    """The application's ``Adw.Application`` subclass.

    Owns exactly one :class:`~winonlinux.task_registry.TaskRegistry` and is responsible for
    installing the :mod:`winonlinux.asyncio_bridge` loop -- both are created lazily, inside the
    first local "activate", never before ``run()`` has resolved whether this process is the
    primary instance.
    """

    def __init__(self, *, cold_start_reference: float | None = None) -> None:
        """Create the application object.

        ``cold_start_reference`` is a ``time.monotonic()`` timestamp taken as early as possible
        in process startup (see :func:`main`), used only to log NFR-1's cold-start-to-interactive
        timing once the main window is first presented. Construction itself does nothing
        side-effecting: no state store, no token cache, no task registry yet -- see the module
        docstring for why that matters for second-instance behavior.
        """
        super().__init__(
            application_id=APPLICATION_ID,
            # Explicitly the default (value 0): NOT Gio.ApplicationFlags.NON_UNIQUE. Spelled out
            # so the single-instance intent is visible at the call site rather than relying on
            # the implicit default -- this is the flag combination that gives single-instance
            # behavior "nearly free" per design.md.
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self._cold_start_reference = cold_start_reference
        self._cold_start_logged = False
        self.task_registry: TaskRegistry | None = None
        self._window: Adw.ApplicationWindow | None = None

        #: Result of the FreeRDP version probe (NFR-8), populated asynchronously shortly after
        #: startup -- ``None`` until that probe task completes. Consumers (e.g. the future
        #: native-launcher change) should treat ``None`` as "not probed yet" and either await
        #: ``self.task_registry`` completing the probe task or re-call
        #: ``winonlinux.freerdp_probe.probe()`` themselves (it is cached, so this is cheap).
        self.freerdp_probe_result: FreeRdpProbeResult | None = None

        #: Non-secret application state (D-13). ``None`` until the single-instance check has
        #: confirmed this process is primary -- see the module docstring for why construction,
        #: not just I/O, waits until inside ``_first_activate``.
        self.state_store: StateStore | None = None

        self.connect("activate", self._on_activate)

    # -- signal handlers -----------------------------------------------------

    def _on_activate(self, _app: "WinOnLinuxApplication") -> None:
        """Handle "activate": create the window on first activation, else just present it.

        This only ever runs locally in the *primary* instance. A second process's ``run()``
        forwards its activation request over D-Bus and this handler fires here, in the first
        process, with no window yet only the very first time; every subsequent call (whether from
        this process re-activating itself or a second launch being forwarded) finds
        ``self._window`` already set and just re-presents it (FR-4-AC-7's "running instance's
        main window is presented").
        """
        if self._window is None:
            self._first_activate()
        self._window.present()

        # Taken here, after present() rather than inside _first_activate(), so the NFR-1 sample
        # actually covers "cold launch to an *interactive* main window" as spec.md's scenario
        # names it -- timestamping before present() would exclude however long GTK itself takes
        # to map/show the window, understating the measurement V3 validates against.
        if self._cold_start_reference is not None and not self._cold_start_logged:
            elapsed_ms = (time.monotonic() - self._cold_start_reference) * 1000.0
            logger.info("cold_start_ms=%d", round(elapsed_ms))
            self._cold_start_logged = True

    def _first_activate(self) -> None:
        """One-time setup performed the first time this instance is actually activated.

        Only reached in the process that won the single-instance race. Installs the asyncio
        bridge, creates the task registry, constructs the state store, schedules the FreeRDP
        probe, and builds the placeholder main window -- in that order, since the window's own
        async work (once the UI-shell change adds any) will need the bridge and registry already
        in place. Nothing here runs in a second (non-primary) process (see module docstring), so
        this is also the only place the state store is ever constructed and the only place the
        FreeRDP probe is ever scheduled -- both satisfy the "second process exits without
        initializing a token cache or state store" scenario by construction.
        """
        asyncio_bridge.install()
        self.task_registry = TaskRegistry()

        # Non-secret state store (D-13). Construction alone does no I/O -- StateStore.load()/
        # .save() are the operations that touch disk -- but it is still built only here, inside
        # the primary instance's one-time setup, rather than at __init__ time, matching the same
        # "nothing side-effecting before app.run() resolves primary-vs-remote" discipline the
        # module docstring documents for the rest of this method.
        self.state_store = StateStore("app", schema_version=1, defaults={})

        # FreeRDP version probe (NFR-8): scheduled under the app-scoped task group so it runs
        # concurrently with window creation rather than blocking startup on a subprocess spawn.
        # self.freerdp_probe_result stays None until the task completes; consumers check that
        # attribute (or await winonlinux.freerdp_probe.probe() themselves -- it is cached, so a
        # second call is cheap) rather than blocking here.
        self.task_registry.get_or_create_group().create_task(
            self._run_freerdp_probe(), name="freerdp-version-probe"
        )

        self._window = Adw.ApplicationWindow(application=self)
        self._window.set_title("WinOnLinux")
        self._window.set_default_size(800, 600)
        # Placeholder content only -- the UI-shell change replaces this with real content.
        self._window.set_content(Gtk.Box())
        # Cold-start timing (NFR-1) is logged in _on_activate, after present() -- see the comment
        # there for why it must not be sampled here, before the window is actually shown.

    async def _run_freerdp_probe(self) -> None:
        """Run the FreeRDP version probe and stash its result on the application instance.

        Runs under the app-scoped task group (see :meth:`_first_activate`); never awaited by
        startup itself, so a slow or hung ``xfreerdp`` invocation (bounded by
        ``freerdp_probe``'s own timeout) cannot delay the interactive window and therefore cannot
        affect the NFR-1 cold-start measurement.
        """
        result = await probe_freerdp()
        self.freerdp_probe_result = result
        logger.debug(
            "freerdp probe result: present=%s version=%s meets_floor=%s",
            result.present,
            result.version,
            result.meets_floor,
        )

    def do_shutdown(self) -> None:
        """Tear down the asyncio bridge cleanly when the application quits."""
        if self.task_registry is not None:
            for account_id in list(self.task_registry.active_account_ids()):
                self.task_registry.destroy_group(account_id)
        if asyncio_bridge.is_installed():
            asyncio_bridge.uninstall()
        Adw.Application.do_shutdown(self)


def main(argv: list[str] | None = None) -> int:
    """Process entry point.

    Captures the cold-start reference timestamp as early as possible, then constructs the
    ``Adw.Application`` and calls ``run()`` immediately -- deliberately nothing else happens in
    between. For a second launch, ``run()``'s internal registration step discovers the instance is
    remote and returns right away; this function never gets far enough to build a state store,
    task registry, or token cache in that process (see module docstring).
    """
    cold_start_reference = time.monotonic()
    app = WinOnLinuxApplication(cold_start_reference=cold_start_reference)
    return app.run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    sys.exit(main())
