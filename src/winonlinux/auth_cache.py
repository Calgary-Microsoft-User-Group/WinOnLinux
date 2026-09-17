"""OS-keyring-backed MSAL token cache (spec.md section 6.3 / "Token cache is keyring-backed", D-2).

D-2 is absolute: per-account tokens (access, refresh, ID, account object) are persisted only via
an OS Secret Service provider (libsecret on Linux). If no such provider is usable at sign-in time,
the application states that plainly and refuses sign-in -- there is no encrypted-file or plaintext
fallback, ever. This module is where that refusal is enforced: every path that cannot prove a
working keyring raises :class:`KeyringUnavailable` rather than silently degrading to something
weaker. In particular ``fallback_to_plaintext`` is always passed as ``False`` to
``msal_extensions.build_persistence`` -- flipping that to ``True`` would reintroduce exactly the
plaintext fallback D-2 forbids, so it must never become a parameter here.

Two entry points other modules integrate against (see the parallel auth_manager.py task):

- :func:`detect_keyring_available` -- a cheap-ish yes/no probe, dispatched through
  ``winonlinux.task_registry.run_blocking`` by its caller (this module never touches the event
  loop itself; every function here is a plain blocking call).
- :func:`build_persisted_cache` -- builds the real, long-lived
  ``msal_extensions.PersistedTokenCache`` handed to ``msal.PublicClientApplication(...,
  token_cache=...)``. Raises :class:`KeyringUnavailable` instead of returning something backed by
  a weaker store.

Both go through the same underlying build-and-health-check path (:func:`build_persisted_cache`
itself, for ``detect_keyring_available``) so there is exactly one notion of "the keyring works" in
this module, per the shared-contract note
that ``detect_keyring_available`` should not maintain a second detection strategy that could
disagree with ``build_persisted_cache``.

Health check without touching the real cache
----------------------------------------------
Construction of a persistence object can succeed even when the backing Secret Service is not
actually reachable (D-Bus session bus present but no keyring daemon behind it is a known failure
mode) -- the failure only surfaces on the first real ``save``/``load``. So this module performs an
actual save/load round trip before declaring the keyring usable. That round trip deliberately runs
against a *separate* dedicated secret item (``_HEALTHCHECK_*`` below), never against the real
token-cache identity -- writing a throwaway probe value through the real persistence object would
overwrite (or, on a mismatched read, appear to corrupt) an already-persisted real cache blob from a
previous session. The real :class:`msal_extensions.PersistedTokenCache` this module returns is
never itself made to ``save``/``load`` by this module; MSAL drives that lifecycle once the caller
starts using it.

ASSUMED msal_extensions API surface -- verify, don't assert
-------------------------------------------------------------
This module cannot be executed in the environment it was written in (no Python/msal-extensions
installed there -- see the task instructions), so the exact shape of ``msal_extensions`` below is
based on the package's documented public surface rather than an interactive check, per CLAUDE.md's
"verify, don't assert" discipline and spec.md section 12 risk 14 (this is the same category of gap
as the FreeRDP version-probe in add-app-foundation). Treat the following as decided-but-unverified
until exercised against a real installed ``msal-extensions`` on Linux (V1):

- ``msal_extensions.build_persistence(location, fallback_to_plaintext=False)`` is the cross-platform
  factory: on Linux it attempts a libsecret-backed persistence and, with
  ``fallback_to_plaintext=False``, propagates the underlying failure (rather than silently handing
  back a plaintext-file persistence) when no Secret Service is reachable. ``location`` is a string
  used as the persisted item's identity; two different ``location`` values are assumed to address
  two independent keyring entries, which is what lets the health check use a location distinct from
  the real cache without touching it.
- The returned persistence object exposes synchronous ``save(str) -> None`` and
  ``load() -> str`` methods (the shape ``msal_extensions.PersistedTokenCache`` itself relies on),
  which is also what this module's health check calls directly.
- ``msal_extensions.PersistedTokenCache(persistence)`` returns an ``msal.SerializableTokenCache``
  subclass suitable to pass as ``msal.PublicClientApplication(..., token_cache=...)`` directly.

If V1 finds any of this wrong, D-17's revisit trigger fires (design.md, "Risks / Trade-offs");
this module's two entry points are the seam that would need to change, not any of the four other
parallel modules that only call them.

Logging discipline (spec.md section 10.7)
--------------------------------------------
Nothing here ever logs a token, a serialized cache blob, or the health-check payload's round-tripped
value -- only coarse facts (a build attempt, whether it ended up usable, the exception's type name
without its full text where that text could conceivably embed a value). Account identifiers and
state transitions are for auth_manager.py's own module to log, not this one.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

try:
    import msal_extensions
except ImportError:  # pragma: no cover - exercised indirectly via KeyringUnavailable below
    msal_extensions = None  # type: ignore[assignment]

__all__ = [
    "KeyringUnavailable",
    "detect_keyring_available",
    "build_persisted_cache",
    "CacheWriteSerializer",
    "cache_write_serializer",
]

logger = logging.getLogger(__name__)

#: Identifies the real, long-lived token cache entry. Never used as the health-check target (see
#: module docstring) so a health check can never clobber a real, already-persisted cache blob.
_CACHE_ITEM_NAME = "msal_token_cache"

#: A dedicated, separate identity used only for the save/load round-trip health check. Distinct
#: from `_CACHE_ITEM_NAME` on purpose.
_HEALTHCHECK_ITEM_NAME = "keyring_healthcheck"

#: Small, fixed, non-secret payload for the round-trip check. Never anything token-shaped, and
#: never logged even though it is not sensitive -- consistent, no-exceptions handling is simpler
#: to audit than "this particular string is fine to log."
_HEALTHCHECK_PAYLOAD = "winonlinux-keyring-healthcheck-v1"


class KeyringUnavailable(Exception):
    """No usable OS Secret Service / keyring backend was found (D-2).

    Callers MUST catch this and enter the D-2 refusal state: a plain explanation and sign-in
    disabled. There is no weaker fallback to degrade to.
    """


def _data_dir() -> Path:
    """Base directory used to build a stable identity string for the token cache entry.

    Per the XDG Base Directory spec, mirroring ``state_store.py``'s convention for
    ``XDG_STATE_HOME`` but using ``XDG_DATA_HOME`` since a token cache is persisted data, not
    transient UI state. On Linux this path is not actually where secret bytes land (those go into
    the Secret Service, not a file -- see the "ASSUMED msal_extensions API surface" note above);
    it only supplies a stable, per-install identity string to `build_persistence`.
    """

    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "winonlinux"


def _location_for(item_name: str, app_name: str) -> str:
    return str(_data_dir() / app_name / f"{item_name}.bin")


def _require_msal_extensions() -> Any:
    if msal_extensions is None:
        raise KeyringUnavailable("msal_extensions is not installed")
    return msal_extensions


def _build_persistence(location: str) -> Any:
    """Build one platform-appropriate persistence object for ``location``.

    Always passes ``fallback_to_plaintext=False`` (D-2 -- see module docstring). Any failure to
    build a working non-plaintext backend is normalized to :class:`KeyringUnavailable`; the
    original exception is chained (``from exc``) for diagnostics, but its text is not logged here
    since some keyring backends are known to embed request/attribute detail in exception messages.
    """

    ext = _require_msal_extensions()
    try:
        return ext.build_persistence(location, fallback_to_plaintext=False)
    except Exception as exc:
        raise KeyringUnavailable(
            f"no usable OS keyring/Secret Service backend ({type(exc).__name__})"
        ) from exc


def _verify_keyring_health(app_name: str) -> None:
    """Round-trip a small, fixed, non-secret payload through a dedicated health-check identity.

    Deliberately never touches ``_CACHE_ITEM_NAME`` -- see module docstring "Health check without
    touching the real cache". Raises :class:`KeyringUnavailable` on any failure to build, save,
    load, or match, so a Secret Service that is present but not actually answering (e.g. no
    keyring daemon behind the D-Bus session bus) is caught here rather than surfacing later as a
    confusing failure deep inside MSAL.
    """

    persistence = _build_persistence(_location_for(_HEALTHCHECK_ITEM_NAME, app_name))
    try:
        persistence.save(_HEALTHCHECK_PAYLOAD)
        round_tripped = persistence.load()
    except Exception as exc:
        raise KeyringUnavailable(
            f"keyring round-trip save/load failed ({type(exc).__name__})"
        ) from exc
    if round_tripped != _HEALTHCHECK_PAYLOAD:
        raise KeyringUnavailable("keyring round-trip returned a mismatched value")


def build_persisted_cache(app_name: str = "winonlinux") -> Any:
    """Build the real ``msal_extensions.PersistedTokenCache`` for ``app_name``.

    Raises :class:`KeyringUnavailable` if no working, non-plaintext OS keyring backend can be
    proven (build failure or health-check failure) -- callers enter the D-2 refusal state on this
    exception, there is no fallback return value.

    The object returned is ``msal.TokenCache``-compatible (an ``msal.SerializableTokenCache``
    subclass) and is passed straight to ``msal.PublicClientApplication(..., token_cache=...)``.
    """

    ext = _require_msal_extensions()
    _verify_keyring_health(app_name)
    persistence = _build_persistence(_location_for(_CACHE_ITEM_NAME, app_name))
    return ext.PersistedTokenCache(persistence)


def detect_keyring_available() -> bool:
    """Best-effort probe: can :func:`build_persisted_cache` succeed right now?

    Synchronous and potentially slow (a D-Bus round trip) -- callers MUST dispatch this through
    ``winonlinux.task_registry.run_blocking``, never call it directly on the event-loop thread.

    Returns ``True``/``False`` and never raises :class:`KeyringUnavailable` itself (that is
    :func:`build_persisted_cache`'s contract, not this one's) -- deliberately reuses the exact same
    build-and-health-check path rather than a second, independent probing strategy that could
    disagree with what a real sign-in attempt would experience.
    """

    try:
        build_persisted_cache()
    except KeyringUnavailable:
        return False
    except Exception:
        # Fail safe toward "unavailable" rather than propagating a surprising, unclassified
        # exception out of a probe function -- a caller should see False and offer the D-2
        # refusal state, not crash. The exception's type is logged for diagnostics; its text is
        # not, per the same caution as `_build_persistence`.
        logger.warning("keyring availability probe raised an unclassified exception", exc_info=False)
        return False
    return True


class CacheWriteSerializer:
    """Async context manager around one process-wide :class:`asyncio.Lock`.

    ``msal_extensions.PersistedTokenCache`` persists the *whole* cache blob on any change,
    including a change that only touches one account's tokens. Two concurrent MSAL calls for
    *different* accounts (e.g. one account's silent refresh racing another account's interactive
    sign-in) could otherwise race on that persist step and corrupt or drop the other's write. Every
    caller wraps an MSAL call that can mutate the cache -- interactive acquisition, silent
    acquisition, ``remove_account`` -- in ``async with cache_write_serializer:``.

    Distinct from the per-*account* single-flight lock in ``auth_manager.py`` (a parallel task),
    which prevents redundant concurrent refreshes for the *same* account; this lock is about safe
    persistence across *different* accounts' concurrent operations and is process-wide by design,
    hence the module-level singleton :data:`cache_write_serializer` below rather than one instance
    per caller.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "CacheWriteSerializer":
        await self._lock.acquire()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._lock.release()


#: The one process-wide instance callers actually use: ``async with cache_write_serializer:``.
cache_write_serializer = CacheWriteSerializer()
