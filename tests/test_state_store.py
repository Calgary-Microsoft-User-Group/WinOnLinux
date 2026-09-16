"""Unit tests for winonlinux.state_store (spec.md: persisted state store, D-13)."""

from __future__ import annotations

import json
import logging
import os

import pytest

from winonlinux.state_store import (
    REDACTED,
    Secret,
    SecretValueRejected,
    StateStore,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolated_xdg_state_home(tmp_path, monkeypatch):
    """Never touch the real filesystem outside tmp_path.

    Sets XDG_STATE_HOME for tests that rely on the environment-variable path, and also removes
    HOME/USERPROFILE fallbacks from view so a bug that ignores XDG_STATE_HOME fails loudly rather
    than silently writing into a real home directory.
    """
    state_home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    return state_home


# -- helpers -----------------------------------------------------------------


def _store(tmp_path, **kwargs):
    kwargs.setdefault("state_home", tmp_path / "explicit-state-home")
    return StateStore("test-store", **kwargs)


# -- schemaVersion / XDG path -------------------------------------------------


def test_path_uses_xdg_state_home_env_var(tmp_path, isolated_xdg_state_home):
    store = StateStore("prefs", schema_version=1)
    assert store.path == isolated_xdg_state_home / "winonlinux" / "prefs.json"


def test_path_falls_back_to_local_state_when_xdg_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(
        "winonlinux.state_store.Path.home", lambda: tmp_path / "home-dir"
    )
    store = StateStore("prefs", schema_version=1)
    assert store.path == tmp_path / "home-dir" / ".local" / "state" / "winonlinux" / "prefs.json"


def test_load_missing_store_returns_defaults_at_current_version(tmp_path):
    store = _store(tmp_path, schema_version=3, defaults={"windowWidth": 800})
    data = store.load()
    assert data == {"schemaVersion": 3, "windowWidth": 800}
    assert not store.path.exists()


# -- migration forward from schemaVersion 0 -----------------------------------


def test_migration_forward_from_version_zero_no_data_loss(tmp_path):
    def migrate_0_to_1(data: dict) -> dict:
        data["migratedFlag"] = True
        return data

    def migrate_1_to_2(data: dict) -> dict:
        data["accountId"] = data.pop("legacyAccountId")
        return data

    store = _store(
        tmp_path,
        schema_version=2,
        migrations={0: migrate_0_to_1, 1: migrate_1_to_2},
    )
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps({"schemaVersion": 0, "legacyAccountId": "abc-123", "keepMe": "yes"}),
        encoding="utf-8",
    )

    loaded = store.load()

    assert loaded["schemaVersion"] == 2
    assert loaded["accountId"] == "abc-123"
    assert loaded["keepMe"] == "yes"  # carried forward untouched, no data loss
    assert loaded["migratedFlag"] is True
    assert "legacyAccountId" not in loaded

    # The store was rewritten on disk at the current version.
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk == loaded


def test_migration_missing_schema_version_treated_as_version_zero(tmp_path):
    def migrate_0_to_1(data: dict) -> dict:
        data["upgraded"] = True
        return data

    store = _store(tmp_path, schema_version=1, migrations={0: migrate_0_to_1})
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"field": "value"}), encoding="utf-8")

    loaded = store.load()
    assert loaded["schemaVersion"] == 1
    assert loaded["field"] == "value"
    assert loaded["upgraded"] is True


def test_migration_missing_step_raises_instead_of_silently_losing_data(tmp_path):
    store = _store(tmp_path, schema_version=2, migrations={})
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"schemaVersion": 0}), encoding="utf-8")

    with pytest.raises(KeyError):
        store.load()


# -- newer-than-understood schemaVersion fails safe ---------------------------


def test_newer_schema_version_leaves_file_untouched_and_uses_defaults(tmp_path):
    store = _store(tmp_path, schema_version=1, defaults={"windowWidth": 640})
    store.path.parent.mkdir(parents=True, exist_ok=True)
    original_payload = {
        "schemaVersion": 99,
        "fromTheFuture": "do-not-touch",
    }
    store.path.write_text(json.dumps(original_payload), encoding="utf-8")
    original_bytes = store.path.read_bytes()
    original_mtime = store.path.stat().st_mtime_ns

    loaded = store.load()

    assert loaded == {"schemaVersion": 1, "windowWidth": 640}
    # The on-disk file must be byte-for-byte untouched: not modified, not truncated.
    assert store.path.read_bytes() == original_bytes
    assert store.path.stat().st_mtime_ns == original_mtime
    assert json.loads(store.path.read_text(encoding="utf-8")) == original_payload


def test_newer_schema_version_does_not_raise(tmp_path):
    store = _store(tmp_path, schema_version=1)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"schemaVersion": 5}), encoding="utf-8")

    # Must not raise.
    store.load()


# -- atomicity -----------------------------------------------------------------


def test_write_is_atomic_original_file_unchanged_on_simulated_crash(tmp_path, monkeypatch):
    store = _store(tmp_path, schema_version=1)
    store.save({"value": "first"})
    original_bytes = store.path.read_bytes()

    def exploding_replace(*_args, **_kwargs):
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(os, "replace", exploding_replace)

    with pytest.raises(OSError):
        store.save({"value": "second-should-not-land"})

    # The original file is exactly as it was before the failed write.
    assert store.path.read_bytes() == original_bytes
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk["value"] == "first"

    # No leftover temp file from the failed write.
    leftovers = [p for p in store.path.parent.iterdir() if p.name != store.path.name]
    assert leftovers == []


def test_write_atomic_uses_temp_file_then_replace(tmp_path, monkeypatch):
    store = _store(tmp_path, schema_version=1)
    calls = []
    real_replace = os.replace

    def spying_replace(src, dst, *args, **kwargs):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", spying_replace)
    store.save({"value": "x"})

    assert len(calls) == 1
    src, dst = calls[0]
    assert src != dst
    assert dst == str(store.path)


# -- secret rejection ------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"accessToken": "some-value"},
        {"account": {"refreshToken": "nested-value"}},
        {"credentials": {"password": "hunter2"}},
        {"authorizationHeader": "Bearer abc"},
        {"items": [{"apiKey": "xyz"}]},
    ],
)
def test_save_refuses_key_shaped_secrets(tmp_path, payload, caplog):
    store = _store(tmp_path, schema_version=1)
    secret_value = json.dumps(payload)  # for substring checks below

    with caplog.at_level(logging.WARNING, logger="winonlinux.state_store"):
        with pytest.raises(SecretValueRejected):
            store.save(payload)

    assert not store.path.exists()

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records, "expected a WARNING log record for the refused write"
    joined = "\n".join(r.getMessage() for r in warning_records)
    assert REDACTED in joined
    # The actual secret value(s) must never appear in the log output.
    for leaf_value in _leaf_string_values(payload):
        assert leaf_value not in joined
    del secret_value


def test_save_refuses_jwt_shaped_value_even_without_secret_key_name(tmp_path, caplog):
    store = _store(tmp_path, schema_version=1)
    jwt_like = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dGhpc2lzYXNpZ25hdHVyZQ"
    payload = {"opaqueField": jwt_like}

    with caplog.at_level(logging.WARNING, logger="winonlinux.state_store"):
        with pytest.raises(SecretValueRejected):
            store.save(payload)

    assert not store.path.exists()
    joined = "\n".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert REDACTED in joined
    assert jwt_like not in joined


def test_save_refuses_opaque_non_jwt_token_under_an_innocuous_key_name(tmp_path, caplog):
    # Real Entra access/refresh tokens are usually opaque (no dot-separated JWT structure) --
    # unlike the JWT-shaped-value test above. This value has no secret-shaped key name AND no
    # JWT structure, so only a value-shape fallback (mirroring logging_setup's opaque-token
    # pattern) can catch it; without it, an opaque token stored under a bland key like "blob"
    # would be persisted to disk in full.
    store = _store(tmp_path, schema_version=1)
    opaque_token = "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0"  # 40 chars, no dots
    payload = {"blob": opaque_token}

    with caplog.at_level(logging.WARNING, logger="winonlinux.state_store"):
        with pytest.raises(SecretValueRejected):
            store.save(payload)

    assert not store.path.exists()
    joined = "\n".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert REDACTED in joined
    assert opaque_token not in joined


def test_save_accepts_short_non_secret_string_under_innocuous_key(tmp_path):
    # Guard against the opaque-token fallback being so broad it rejects ordinary short strings --
    # only long (32+ char), dot-free runs are treated as token-shaped.
    store = _store(tmp_path, schema_version=1, defaults={})
    store.save({"lastAccountHint": "not-a-secret-looking-string"})
    assert store.path.exists()


def test_save_refuses_explicit_secret_wrapper(tmp_path, caplog):
    store = _store(tmp_path, schema_version=1)
    payload = {"someField": Secret("do-not-persist-me")}

    with caplog.at_level(logging.WARNING, logger="winonlinux.state_store"):
        with pytest.raises(SecretValueRejected):
            store.save(payload)

    assert not store.path.exists()
    joined = "\n".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert REDACTED in joined
    assert "do-not-persist-me" not in joined


def test_save_refusal_does_not_disturb_existing_file(tmp_path):
    store = _store(tmp_path, schema_version=1)
    store.save({"safeField": "ok"})
    original_bytes = store.path.read_bytes()

    with pytest.raises(SecretValueRejected):
        store.save({"safeField": "ok", "password": "hunter2"})

    assert store.path.read_bytes() == original_bytes


def test_save_accepts_non_secret_data_and_round_trips(tmp_path):
    store = _store(tmp_path, schema_version=1, defaults={})
    store.save({"windowWidth": 1024, "lastAccountHint": "not-a-secret-looking-string"})

    reloaded = StateStore(
        "test-store", schema_version=1, state_home=store._state_home
    ).load()
    assert reloaded["windowWidth"] == 1024
    assert reloaded["lastAccountHint"] == "not-a-secret-looking-string"
    assert reloaded["schemaVersion"] == 1


def _leaf_string_values(payload):
    if isinstance(payload, dict):
        for value in payload.values():
            yield from _leaf_string_values(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _leaf_string_values(item)
    elif isinstance(payload, str):
        yield payload
