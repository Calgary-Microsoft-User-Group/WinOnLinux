"""Per-resource connection-method preference store (add-ui-shell change, tasks.md 1.5).

FR-2-AC-2: the split button's primary action is the resource's last-used method, persisted per
resource **per account** across restart. Backed by the app-foundation
:class:`~winonlinux.state_store.StateStore` (D-13: JSON under ``XDG_STATE_HOME`` with its own
``schemaVersion``); nothing here is secret-shaped, and the store's own secret-refusal heuristics
run on every save regardless.

Schema (v1), per design.md: ``{"preferences": {home_account_id: {resource_key: "native"|"web"}}}``
where the resource key is the Graph ``id`` for Cloud PCs (FR-1-AC-2) and
``workspace_id/resource_id`` for AVD bookmarks (see :func:`winonlinux.ui_models.avd_resource_key`).

Synchronous disk I/O (delegates to ``StateStore``): callers on the event-loop thread dispatch
``load``/``record`` through ``run_blocking`` (D-18; the F-21 calling convention).
"""

from __future__ import annotations

import logging

from winonlinux.state_store import StateStore
from winonlinux.ui_models import ConnectMethod

__all__ = ["MethodPreferenceStore", "DEFAULT_METHOD"]

logger = logging.getLogger(__name__)

_STORE_NAME = "method_preferences"
_SCHEMA_VERSION = 1

#: The primary method when no preference is recorded yet. Web, deliberately: it is the only
#: method any near-term build can dispatch (native arrives with add-native-launcher and is
#: further gated on Stage 0/1), and a first-run primary action that cannot work would violate
#: the spirit of §4.2 even while rendering correctly.
DEFAULT_METHOD = ConnectMethod.WEB


class MethodPreferenceStore:
    """Remembers each resource's last-used connect method, keyed by account then resource."""

    def __init__(self, *, state_home=None) -> None:
        self._store = StateStore(
            _STORE_NAME,
            schema_version=_SCHEMA_VERSION,
            defaults={"preferences": {}},
            state_home=state_home,
        )
        self._preferences: dict[str, dict[str, str]] = {}
        self._loaded = False

    def load(self) -> None:
        """Read the store into memory. Malformed shapes degrade to defaults (a preference file
        is convenience data -- never worth failing startup over)."""
        data = self._store.load()
        raw = data.get("preferences")
        self._preferences = {}
        if isinstance(raw, dict):
            for account_id, per_resource in raw.items():
                if not isinstance(per_resource, dict):
                    continue
                kept = {
                    key: value
                    for key, value in per_resource.items()
                    if isinstance(value, str)
                    and value in (ConnectMethod.NATIVE.value, ConnectMethod.WEB.value)
                }
                if kept:
                    self._preferences[str(account_id)] = kept
        self._loaded = True

    def preferred_method(self, home_account_id: str, resource_key: str) -> ConnectMethod:
        """The remembered last-used method, or :data:`DEFAULT_METHOD` when none is recorded."""
        value = self._preferences.get(home_account_id, {}).get(resource_key)
        if value is None:
            return DEFAULT_METHOD
        return ConnectMethod(value)

    def record_use(
        self, home_account_id: str, resource_key: str, method: ConnectMethod
    ) -> None:
        """Record a launch's method and persist immediately (FR-2-AC-2: survives restart --
        deferring the write would lose it on an unclean exit)."""
        self._preferences.setdefault(home_account_id, {})[resource_key] = method.value
        self._store.save({"preferences": self._preferences})

    def forget_account(self, home_account_id: str) -> None:
        """Drop one account's preferences (called on sign-out, FR-3-AC-3's spirit: a signed-out
        account leaves no per-account state behind) and persist the removal."""
        if self._preferences.pop(home_account_id, None) is not None:
            self._store.save({"preferences": self._preferences})
