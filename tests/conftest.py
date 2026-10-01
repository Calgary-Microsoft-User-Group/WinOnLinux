"""Shared pytest fixtures and helpers for the WinOnLinux test suite."""

from __future__ import annotations

import sys
import types


def install_gi_stub() -> None:
    """Install a minimal ``gi`` module stub if the real PyGObject is absent.

    PyGObject is not installed in CI (spec.md §13.4: unit tests need no display server); this
    stub provides just enough of the ``gi`` surface for ``winonlinux.app`` and
    ``winonlinux.asyncio_bridge`` to import and for their non-GTK behavior to be exercised.
    Anything needing a real GTK main loop stays manual (``tests/manual/``).
    """
    if "gi" in sys.modules:
        return

    class _ApplicationFlags:
        FLAGS_NONE = 0
        NON_UNIQUE = 8

    class _Application:
        def __init__(self, **kwargs) -> None:
            self._stub_init_kwargs = kwargs

        def connect(self, *_args, **_kwargs) -> int:
            return 0

        def run(self, _argv=None) -> int:
            return 0

        def do_shutdown(self) -> None:  # pragma: no cover - not exercised headlessly
            pass

    class _ApplicationWindow:  # pragma: no cover - _first_activate is not exercised headlessly
        def __init__(self, **kwargs) -> None:
            pass

    gi = types.ModuleType("gi")
    gi.require_version = lambda *_a, **_k: None  # type: ignore[attr-defined]
    repository = types.ModuleType("gi.repository")
    repository.Adw = types.SimpleNamespace(  # type: ignore[attr-defined]
        Application=_Application, ApplicationWindow=_ApplicationWindow
    )
    repository.Gio = types.SimpleNamespace(ApplicationFlags=_ApplicationFlags)  # type: ignore[attr-defined]
    repository.Gtk = types.SimpleNamespace(Box=lambda *a, **k: object())  # type: ignore[attr-defined]
    repository.GLib = types.SimpleNamespace(  # type: ignore[attr-defined]
        PRIORITY_DEFAULT=0,
        SOURCE_CONTINUE=True,
        SOURCE_REMOVE=False,
        idle_add=lambda *_a, **_k: 1,
        source_remove=lambda *_a: None,
    )
    gi.repository = repository  # type: ignore[attr-defined]
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repository
