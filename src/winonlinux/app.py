"""Application shell: single-instance ``Adw.Application`` entry point.

Implements the "Single application instance" requirement (spec.md, add-app-foundation change,
FR-4-AC-7, §6.3) and wires in the other pieces this change adds: the GLib<->asyncio bridge
(``winonlinux.asyncio_bridge``, D-18), the per-account task-group registry
(``winonlinux.task_registry``), the FreeRDP version probe (``winonlinux.freerdp_probe``, NFR-8),
and the non-secret state store (``winonlinux.state_store``, D-13). Also constructs and starts the
Auth/Account Manager (``winonlinux.auth_manager``, add-auth-account-manager change) -- see
``_first_activate`` and ``_start_auth_manager`` below -- and registers an active-account listener
that starts CloudPC polling and runs an initial enumeration (``winonlinux.cloudpc_provider``,
add-cloudpc-enumeration change) every time ``AuthManager`` establishes or changes the active
account -- on startup rebuild, on the first interactive sign-in, and on every account switch -- see
``_on_active_account_changed`` below.

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
from winonlinux.auth_cache import KeyringUnavailable
from winonlinux.auth_manager import AuthManager
from winonlinux.avd_bookmarks import BookmarkStore
from winonlinux.cloudpc_provider import CloudPcProvider
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

        #: The auth/account manager (add-auth-account-manager change). Constructed synchronously
        #: inside ``_first_activate`` -- immediately after ``self.task_registry`` -- so other code
        #: can reach ``self.auth_manager`` (e.g. to read ``.accounts``) even before its async
        #: ``start()`` has finished; ``start()`` itself is scheduled as a background task and never
        #: awaited here (see ``_first_activate`` and ``_start_auth_manager`` below). ``None`` only
        #: before the single-instance check confirms this process is primary, matching
        #: ``state_store``'s and the FreeRDP probe's discipline.
        self.auth_manager: AuthManager | None = None

        #: Populated only if the scheduled ``_start_auth_manager`` task fails -- most notably with
        #: :class:`~winonlinux.auth_cache.KeyringUnavailable` (D-2: no usable OS keyring, sign-in
        #: must be refused with no fallback). ``winonlinux.task_registry.TaskRegistry`` has no
        #: built-in convention for surfacing a background task's exception (a task's done-callback
        #: only discards it from the tracked set -- see task_registry.py); this attribute is where
        #: that exception is stashed instead of letting it disappear silently, following the same
        #: "stash the async result on the application instance, `None` means not finished yet"
        #: pattern already used for ``freerdp_probe_result``. A future UI-shell change reads this
        #: (or catches it live via a comment/notification path of its own) to show the D-2 refusal
        #: state (spec.md's "plain explanation, sign-in disabled, no fallback") -- presenting that
        #: UI is out of scope for this change (see auth_manager.py's own docstring).
        self.auth_manager_start_error: BaseException | None = None

        #: Windows 365 Cloud PC enumeration/refresh provider (add-cloudpc-enumeration change).
        #: Constructed synchronously inside ``_first_activate``, immediately after
        #: ``self.auth_manager`` -- construction alone does no I/O (no Graph call, no polling
        #: task) -- matching every other attribute's "construct here, do I/O later" discipline in
        #: this method. ``None`` only before the single-instance check confirms this process is
        #: primary. See ``_start_auth_manager`` for when polling/the first refresh actually start.
        self.cloudpc_provider: CloudPcProvider | None = None

        #: Phase 0 AVD bookmark store (add-cloudpc-enumeration change, spec.md section 11).
        #: Constructed synchronously alongside ``cloudpc_provider`` -- construction alone does no
        #: I/O; ``BookmarkStore.load()`` is not called here (that is a future UI-shell change's
        #: job, when it has somewhere to render the result). ``None`` until the single-instance
        #: check confirms this process is primary.
        self.avd_bookmarks: BookmarkStore | None = None

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

        # Auth/Account Manager (add-auth-account-manager change). Constructed synchronously, right
        # here, so self.auth_manager is reachable immediately -- but its async start() (keyring
        # build + cached-account enumeration, both potentially slow I/O) is only *scheduled* below,
        # under the app-scoped task group, exactly like the FreeRDP probe: never awaited inline, so
        # a slow or unavailable keyring cannot delay window creation or the NFR-1 cold-start
        # measurement.
        self.auth_manager = AuthManager(task_registry=self.task_registry)

        # CloudPC provider + Phase 0 AVD bookmarks (add-cloudpc-enumeration change). Constructed
        # synchronously, right here, immediately after self.auth_manager -- construction alone
        # does no I/O (no Graph call, no polling task, no BookmarkStore.load()), matching this
        # method's "construct now, do I/O later" discipline for every other attribute above.
        self.cloudpc_provider = CloudPcProvider(
            auth_manager=self.auth_manager, task_registry=self.task_registry
        )
        self.avd_bookmarks = BookmarkStore()

        # Registered BEFORE auth_manager.start() is scheduled below, so the startup-rebuild case
        # (a cached account made active inside start() itself) fires this listener too, uniformly
        # with a later interactive sign-in or account switch -- see _on_active_account_changed.
        self.auth_manager.add_active_account_listener(self._on_active_account_changed)

        self.task_registry.get_or_create_group().create_task(
            self._start_auth_manager(), name="auth-manager-start"
        )

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

    async def _start_auth_manager(self) -> None:
        """Run ``self.auth_manager.start()`` and never let its exception disappear silently.

        Runs under the app-scoped task group (see :meth:`_first_activate`); never awaited by
        startup itself, so keyring I/O or MSAL setup work cannot delay the interactive window.

        ``winonlinux.task_registry.TaskRegistry`` has no built-in convention for surfacing a
        background task's exception -- a task's done-callback only discards it from the tracked
        set (see task_registry.py's ``_discard``) -- so this method follows the same convention
        ``_run_freerdp_probe`` uses for its *result*: catch here, stash the outcome on the
        application instance, log it, and let readers poll the attribute rather than relying on an
        unhandled-exception warning from asyncio's default task-exception logging.

        :class:`~winonlinux.auth_cache.KeyringUnavailable` (D-2: no usable OS Secret Service) is
        the specific, expected failure mode -- logged at WARNING, since it is not a bug, it is the
        documented no-fallback refusal state. Anything else is logged at ERROR with a traceback,
        since ``AuthManager.start()`` is not otherwise expected to raise. Either way the exception
        is stashed on :attr:`auth_manager_start_error` (``None`` while this task is still running
        or once it has succeeded) rather than propagated -- there is no caller here to catch it,
        and letting it escape this task would only produce asyncio's generic "exception was never
        retrieved" warning instead of an actionable log line.

        """
        try:
            await self.auth_manager.start()
        except KeyringUnavailable as exc:
            self.auth_manager_start_error = exc
            logger.warning(
                "AuthManager.start() found no usable OS keyring (D-2 refusal state: sign-in is "
                "disabled, there is no fallback): %s",
                exc,
            )
        except Exception as exc:  # noqa: BLE001 - deliberately broad: never let this disappear
            self.auth_manager_start_error = exc
            logger.error("AuthManager.start() failed unexpectedly", exc_info=True)
        # No further action here: if start() resolved an active account, it already called the
        # _on_active_account_changed listener registered in _first_activate synchronously, before
        # this coroutine even resumes -- see AuthManager.start()'s own _notify_active_account_changed
        # call and this class's _on_active_account_changed below.

    def _on_active_account_changed(self, home_account_id: str) -> None:
        """Registered on :attr:`auth_manager` in :meth:`_first_activate` via
        ``AuthManager.add_active_account_listener`` (add-cloudpc-enumeration change, tasks.md 3.1).

        Fires synchronously, from inside ``AuthManager``, in exactly three cases: ``start()``'s
        startup rebuild resolving a cached account as active, ``add_account()`` establishing the
        very first account, and every ``switch_active_account()`` call that actually changes the
        active account (called there *after* the outgoing account's task group has already been
        cancelled -- see ``switch_active_account``'s own comment on that ordering). This covers
        "sign-in" and "account switch", two of tasks.md 3.1's three named triggers; "manual
        refresh" still has no UI control to invoke it from and remains a future UI-shell change's
        job to wire directly to ``cloudpc_provider.refresh_now``.

        Schedules the actual work under ``home_account_id``'s OWN task group -- not awaited here,
        since this callback itself runs synchronously inside ``AuthManager`` and must return
        immediately -- so that a later switch away from this account cancels it via the same
        ``task_registry.cancel_group`` mechanism ``switch_active_account`` already uses (FR-3-AC-2).
        """
        self.task_registry.get_or_create_group(home_account_id).create_task(
            self._enumerate_active_account(home_account_id), name="cloudpc-enumerate-on-account-change"
        )

    async def _enumerate_active_account(self, home_account_id: str) -> None:
        """Start CloudPC polling and run one enumeration for ``home_account_id``.

        Runs under ``home_account_id``'s own task group (see :meth:`_on_active_account_changed`),
        so it is cancelled automatically if the active account changes again before it finishes.

        The :meth:`CloudPcProvider.refresh_now` call is awaited (not fire-and-forgotten) so
        ``self.cloudpc_provider.last_result_for(home_account_id)`` is populated by the time this
        task completes -- but
        any exception it raises (including an ``AuthError`` subclass propagating straight out of
        ``refresh_now``, e.g. ``ReauthRequiredError`` for a cached account whose refresh token has
        since been revoked) is caught and logged here rather than left to asyncio's generic
        "exception was never retrieved" task-exception warning: ``last_result_for`` already carries a
        typed outcome for a future UI to read (or stays whatever it was before, if this call
        raised before producing a new one), and there is no caller here that could usefully react
        to the exception itself.
        """
        self.cloudpc_provider.start_polling(home_account_id)
        try:
            await self.cloudpc_provider.refresh_now(home_account_id)
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
            logger.warning(
                "CloudPC enumeration for account %s failed (%s); the provider's per-account result "
                "reflects whatever outcome was produced, if any",
                home_account_id,
                type(exc).__name__,
                exc_info=True,
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
