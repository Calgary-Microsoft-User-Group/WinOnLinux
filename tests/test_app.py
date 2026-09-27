"""Unit tests for winonlinux.app's non-GTK-dependent behavior.

Uses the shared ``gi`` stub from tests/conftest.py (PyGObject is not installed in CI, spec.md
§13.4) -- just enough for the module to import and ``main()`` to construct the application object
and call ``run()``. Anything needing a real GTK main loop stays manual (``tests/manual/``).
"""

from __future__ import annotations

import logging

import pytest

from tests.conftest import install_gi_stub

pytestmark = pytest.mark.unit


def test_main_installs_redaction_before_run(monkeypatch, caplog):
    """Every entry path runs redacted (fix-redaction-hardening, audit F-15): app.main() installs
    the §10.7 record factory BEFORE run(), so direct execution of app.py is equivalent to
    ``python -m winonlinux`` -- a token logged from inside run() must already be redacted."""
    install_gi_stub()
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
