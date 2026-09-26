"""Unit tests for winonlinux.app's non-GTK-dependent behavior.

PyGObject is not installed in CI (spec.md §13.4: unit tests need no display server), so this
module installs a minimal stub of the ``gi`` surface ``app.py``/``asyncio_bridge.py`` import --
just enough for the module to import and ``main()`` to construct the application object and call
``run()``. Anything needing a real GTK main loop stays manual (``tests/manual/``).
"""

from __future__ import annotations

import logging
import sys
import types

import pytest

pytestmark = pytest.mark.unit


def _install_gi_stub() -> None:
    """Install a minimal ``gi`` module stub if the real PyGObject is absent."""
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

        def do_shutdown(self) -> None:  # pragma: no cover - not exercised here
            pass

    class _ApplicationWindow:  # pragma: no cover - _first_activate is not exercised here
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
        idle_add=lambda *a, **k: 1,
        source_remove=lambda *_a: None,
    )
    gi.repository = repository  # type: ignore[attr-defined]
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repository


def test_main_installs_redaction_before_run(monkeypatch, caplog):
    """Every entry path runs redacted (fix-redaction-hardening, audit F-15): app.main() installs
    the §10.7 record factory BEFORE run(), so direct execution of app.py is equivalent to
    ``python -m winonlinux`` -- a token logged from inside run() must already be redacted."""
    _install_gi_stub()
    from winonlinux import app as app_module

    fake_token = "direct-entry-path-token-that-must-not-leak-0123456789"

    def fake_run(self, _argv=None) -> int:
        # Simulates the first component logging during the app's lifetime: by this point the
        # record factory must already be installed, whichever entry path was used.
        logging.getLogger("winonlinux.test.app-entry").warning("token %s", fake_token)
        return 0

    monkeypatch.setattr(app_module.WinOnLinuxApplication, "run", fake_run)

    with caplog.at_level(logging.DEBUG):
        rc = app_module.main([])

    assert rc == 0
    leaked = fake_token in caplog.text
    assert not leaked, "a token logged during run() was not redacted on the direct entry path"
