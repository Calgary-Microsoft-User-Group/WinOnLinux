"""``python -m winonlinux`` entry point.

Pure glue, per CLAUDE.md's "this is glue, not new logic": configure logging first (before
anything else in the process logs), then hand off to :func:`winonlinux.app.main`, which is
responsible for the cold-start timestamp, constructing the ``Adw.Application``, and installing
the asyncio bridge in the correct order relative to the single-instance check (see ``app.py``'s
module docstring for why nothing side-effecting may run before ``app.run()`` resolves primary vs.
remote instance).

Deliberately does *not* call ``asyncio_bridge.install()`` itself: that must happen only after
``Adw.Application.run()`` has confirmed this process is the primary instance (a second process
that loses the single-instance race must never install a loop, create a state store, or do any
other setup work), so ``app.py``'s ``_first_activate()`` -- reached only via a real local
"activate" -- is where the bridge install already happens. Duplicating it here would install a
loop in every process, including ones that immediately hand off to an existing instance and exit.
"""

from __future__ import annotations

import sys

from winonlinux.app import main
from winonlinux.logging_setup import configure_logging


def _main(argv: list[str] | None = None) -> int:
    """Configure logging, then run the application."""
    configure_logging()
    return main(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    sys.exit(_main())
