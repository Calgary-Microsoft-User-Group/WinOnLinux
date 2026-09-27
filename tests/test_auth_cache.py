"""Unit tests for winonlinux.auth_cache (spec.md: keyring-backed token cache, D-2).

``msal_extensions`` itself is monkeypatched out at the module-attribute level for every test here
-- these tests never touch a real OS Secret Service/keyring, which this environment (and CI) both
lack. Replacing ``auth_cache.msal_extensions`` wholesale (rather than patching individual
functions on the real package) also means these tests pass unchanged whether or not the real
``msal-extensions`` package happens to be importable wherever they run.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from winonlinux import auth_cache
from winonlinux.auth_cache import (
    CacheWriteSerializer,
    KeyringUnavailable,
    build_persisted_cache,
    detect_keyring_available,
)

pytestmark = pytest.mark.unit


class _FakePersistence:
    """Stand-in for an msal_extensions persistence object: save/load against a shared dict."""

    def __init__(self, location: str, store: dict[str, str]) -> None:
        self.location = location
        self._store = store

    def save(self, content: str) -> None:
        self._store[self.location] = content

    def load(self) -> str | None:
        return self._store.get(self.location)


class _FakePersistedTokenCache:
    """Stand-in for msal_extensions.PersistedTokenCache -- just remembers its persistence."""

    def __init__(self, persistence: _FakePersistence) -> None:
        self.persistence = persistence


def _make_fake_msal_extensions(*, build_fails: bool = False) -> types.SimpleNamespace:
    """A fake ``msal_extensions`` module: records every build_persistence call and location."""

    store: dict[str, str] = {}
    calls: list[tuple[str, bool]] = []

    def build_persistence(location: str, fallback_to_plaintext: bool = False) -> _FakePersistence:
        calls.append((location, fallback_to_plaintext))
        if build_fails:
            raise RuntimeError("no Secret Service provider (simulated)")
        return _FakePersistence(location, store)

    fake = types.SimpleNamespace(
        build_persistence=build_persistence,
        PersistedTokenCache=_FakePersistedTokenCache,
    )
    fake.calls = calls
    fake.store = store
    return fake


# -- detect_keyring_available -----------------------------------------------------------------


def test_detect_keyring_available_true_when_build_succeeds(monkeypatch):
    fake = _make_fake_msal_extensions()
    monkeypatch.setattr(auth_cache, "msal_extensions", fake)

    assert detect_keyring_available() is True


def test_detect_keyring_available_false_when_build_fails(monkeypatch):
    fake = _make_fake_msal_extensions(build_fails=True)
    monkeypatch.setattr(auth_cache, "msal_extensions", fake)

    # Must return False, not raise KeyringUnavailable -- that exception is
    # build_persisted_cache()'s contract, not this function's.
    assert detect_keyring_available() is False


def test_detect_keyring_available_false_when_msal_extensions_missing(monkeypatch):
    monkeypatch.setattr(auth_cache, "msal_extensions", None)

    assert detect_keyring_available() is False


# -- build_persisted_cache --------------------------------------------------------------------


def test_build_persisted_cache_raises_keyring_unavailable_when_build_fails(monkeypatch):
    fake = _make_fake_msal_extensions(build_fails=True)
    monkeypatch.setattr(auth_cache, "msal_extensions", fake)

    with pytest.raises(KeyringUnavailable):
        build_persisted_cache()


def test_build_persisted_cache_returns_persisted_token_cache_when_build_succeeds(monkeypatch):
    fake = _make_fake_msal_extensions()
    monkeypatch.setattr(auth_cache, "msal_extensions", fake)

    cache = build_persisted_cache(app_name="testapp")

    assert isinstance(cache, _FakePersistedTokenCache)
    assert isinstance(cache.persistence, _FakePersistence)


def test_build_persisted_cache_health_check_never_touches_real_cache_identity(monkeypatch):
    """The health-check round trip must use a location distinct from the real cache's, so a
    health check can never overwrite (or appear to corrupt) an already-persisted real cache blob
    from a previous session."""

    fake = _make_fake_msal_extensions()
    monkeypatch.setattr(auth_cache, "msal_extensions", fake)

    cache = build_persisted_cache(app_name="testapp")

    locations = [location for location, _fallback in fake.calls]
    assert len(locations) == 2
    healthcheck_location, real_location = locations
    assert healthcheck_location != real_location
    # The health check's own probe value must have been round-tripped through its own location,
    # not the real cache's -- so the real cache's backing store entry is untouched (never saved
    # to at all by this module; MSAL itself drives that later).
    assert fake.store[healthcheck_location] == "winonlinux-keyring-healthcheck-v1"
    assert real_location not in fake.store

    # Every build_persistence call must forbid the D-2-forbidden plaintext fallback.
    assert all(fallback is False for _location, fallback in fake.calls)
    assert cache.persistence.location == real_location


def test_build_persisted_cache_raises_when_msal_extensions_missing(monkeypatch):
    monkeypatch.setattr(auth_cache, "msal_extensions", None)

    with pytest.raises(KeyringUnavailable):
        build_persisted_cache()


# -- CacheWriteSerializer ---------------------------------------------------------------------


def test_cache_write_serializer_mutually_excludes_concurrent_holders():
    async def scenario() -> list[str]:
        serializer = CacheWriteSerializer()
        events: list[str] = []

        async def holder(name: str, hold_seconds: float) -> None:
            async with serializer:
                events.append(f"{name}-start")
                await asyncio.sleep(hold_seconds)
                events.append(f"{name}-end")

        # "a" is created first and so acquires the lock first; if the lock did not actually
        # serialize the two blocks, "b-start" would land between "a-start" and "a-end".
        await asyncio.gather(holder("a", 0.05), holder("b", 0.0))
        return events

    events = asyncio.run(scenario())

    assert events == ["a-start", "a-end", "b-start", "b-end"]


def test_cache_write_serializer_releases_lock_on_exception():
    async def scenario() -> None:
        serializer = CacheWriteSerializer()

        with pytest.raises(ValueError):
            async with serializer:
                raise ValueError("boom")

        # The lock must have been released despite the exception, or this second acquisition
        # would hang forever.
        async with serializer:
            pass

    asyncio.run(asyncio.wait_for(scenario(), timeout=5.0))


def test_no_module_level_serializer_singleton_exists():
    """The import-time singleton is deliberately GONE (fix-shutdown-loop-hygiene, audit F-09):
    an asyncio.Lock created at import binds to the first event loop that contends it and raises
    from any other loop. AuthManager owns one serializer per instance instead -- pinned in
    tests/test_auth_manager.py."""
    assert not hasattr(auth_cache, "cache_write_serializer")


def test_serializer_instances_are_loop_affine_not_cross_loop():
    """Two serializer instances used (contended) in two different event loops must not
    interfere -- the exact failure shape the 2026-09-22 audit reproduced on Python 3.12 with
    the old shared singleton."""

    def contend(serializer: CacheWriteSerializer) -> None:
        async def one_holder():
            async with serializer:
                await asyncio.sleep(0.001)

        async def scenario():
            await asyncio.gather(one_holder(), one_holder())  # forces lock contention

        asyncio.run(scenario())

    # Each loop gets its own instance (as each AuthManager now constructs its own): no
    # cross-loop RuntimeError, unlike a shared instance contended in two loops.
    contend(CacheWriteSerializer())
    contend(CacheWriteSerializer())
