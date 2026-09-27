"""Unit tests for winonlinux.method_prefs (add-ui-shell change, tasks.md 1.5/1.6, FR-2-AC-2)."""

from __future__ import annotations

import pytest

from winonlinux.method_prefs import DEFAULT_METHOD, MethodPreferenceStore
from winonlinux.ui_models import ConnectMethod

pytestmark = pytest.mark.unit


def _store(tmp_path) -> MethodPreferenceStore:
    store = MethodPreferenceStore(state_home=tmp_path / "state")
    store.load()
    return store


def test_default_method_when_nothing_recorded(tmp_path):
    store = _store(tmp_path)
    assert store.preferred_method("acct-a", "cpc-1") is DEFAULT_METHOD


def test_last_used_method_survives_restart(tmp_path):
    """FR-2-AC-2: connect via Web, 'relaunch' (fresh store instance over the same state home),
    and the resource's primary action is still Web -- and native likewise."""
    first = _store(tmp_path)
    first.record_use("acct-a", "cpc-1", ConnectMethod.WEB)
    first.record_use("acct-a", "ws-fin/d1", ConnectMethod.NATIVE)

    relaunched = _store(tmp_path)  # same state home = same on-disk file
    assert relaunched.preferred_method("acct-a", "cpc-1") is ConnectMethod.WEB
    assert relaunched.preferred_method("acct-a", "ws-fin/d1") is ConnectMethod.NATIVE


def test_preferences_are_scoped_per_account(tmp_path):
    store = _store(tmp_path)
    store.record_use("acct-a", "cpc-1", ConnectMethod.NATIVE)
    # Same resource key under a different account is untouched (design.md key rule).
    assert store.preferred_method("acct-b", "cpc-1") is DEFAULT_METHOD


def test_forget_account_drops_only_that_accounts_preferences(tmp_path):
    store = _store(tmp_path)
    store.record_use("acct-a", "cpc-1", ConnectMethod.NATIVE)
    store.record_use("acct-b", "cpc-9", ConnectMethod.NATIVE)

    store.forget_account("acct-a")

    relaunched = _store(tmp_path)
    assert relaunched.preferred_method("acct-a", "cpc-1") is DEFAULT_METHOD
    assert relaunched.preferred_method("acct-b", "cpc-9") is ConnectMethod.NATIVE


def test_malformed_on_disk_shapes_degrade_to_defaults(tmp_path):
    store = _store(tmp_path)
    store._store.save({"preferences": {"acct-a": "not-a-dict", "acct-b": {"cpc": "teleport"}}})

    relaunched = _store(tmp_path)
    assert relaunched.preferred_method("acct-a", "anything") is DEFAULT_METHOD
    assert relaunched.preferred_method("acct-b", "cpc") is DEFAULT_METHOD
