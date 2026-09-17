"""Phase 0 AVD bookmarks: admin-provisioned AVD desktop/RemoteApp entries.

Implements the "Phase 0 AVD entries come from admin-provisioned bookmarks" requirement
(spec.md section 11, add-cloudpc-enumeration design.md: "Bookmarks are configuration, not
discovery"):

- Until the AVD feed protocol lands (``add-avd-feed-provider``), an ordinary end user cannot
  self-enumerate AVD workspaces/desktops/RemoteApps -- Graph has no delegated permission that
  exposes them, and the ARM objects require Azure RBAC an ordinary user lacks. Phase 0 instead
  renders a static list of ``{workspaceId, resourceId, displayName, kind}`` bookmarks that a
  tenant admin supplies out of band (see ``docs/avd-bookmarks-admin-guide.md``).
- These bookmarks are persisted with :class:`winonlinux.state_store.StateStore` (D-13):
  ``schemaVersion`` plus a forward-only migration path, same as every other store in this app.
- Bookmark IDs are opaque to this client -- a stale or mistyped ID is not validated here or at
  render time. It fails at web launch in the browser instead, which is Microsoft's own error
  surface for a bad workspace/resource ID. That is a documented trade-off (design.md), not a bug
  to route around in this module.
- Phase 0 AVD entries are web-launch only. The native (FreeRDP) connect path is disabled for
  every bookmark with :data:`NATIVE_DISABLED_REASON` as the reason a future UI-shell change
  displays -- Phase 0 has no way to obtain the ``.rdpw`` connection configuration a native
  launch needs (that requires the feed work), so this module, which owns the bookmark data,
  also owns the reason string rather than leaving the UI to invent one.

This module only depends on :mod:`winonlinux.state_store` (already implemented) -- it does not
import ``graph_client`` or ``cloudpc_provider``, which are independent, parallel pieces of this
same change.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from winonlinux.state_store import StateStore

__all__ = [
    "AvdBookmark",
    "NATIVE_DISABLED_REASON",
    "BookmarkStore",
    "group_by_workspace",
]

logger = logging.getLogger(__name__)

#: Kinds a bookmark's ``kind`` field may take. Anything else is a validation error -- an admin
#: typo here would otherwise silently produce a bookmark that renders with the wrong icon/launch
#: behavior (spec.md section 5.3 distinguishes desktop vs. RemoteApp entries).
_VALID_KINDS = ("desktop", "remoteapp")

#: Reason a future UI-shell change displays for why the native (FreeRDP) connect method is
#: disabled on every Phase 0 AVD bookmark entry (FR-2-AC-1). Phase 0 has no AVD feed access, so
#: no `.rdpw` connection configuration can be composed for these resources yet -- this is *not*
#: the "workspace/resource ID unknown" case (the IDs are known; they're admin-supplied), it is
#: that the client cannot yet acquire connection configuration at all for AVD resources.
NATIVE_DISABLED_REASON = "Connection configuration cannot be acquired for this resource yet"

#: Current schema version for the "avd_bookmarks" state store (D-13).
_SCHEMA_VERSION = 1

#: Store name -> file becomes ``$XDG_STATE_HOME/winonlinux/avd_bookmarks.json``.
_STORE_NAME = "avd_bookmarks"

_REQUIRED_FIELDS = ("workspaceId", "resourceId", "displayName", "kind")


@dataclasses.dataclass(frozen=True)
class AvdBookmark:
    """One admin-provisioned AVD desktop or RemoteApp entry.

    Parameters
    ----------
    workspace_id:
        ARM object ID of the AVD workspace (from ``Get-AzWvdWorkspace``).
    resource_id:
        ARM object ID of the specific desktop or RemoteApp application (from
        ``Get-AzWvdDesktop`` / ``Get-AzWvdApplication``).
    display_name:
        Name to render in the UI.
    kind:
        Either ``"desktop"`` or ``"remoteapp"``. Anything else raises :class:`ValueError` --
        this is validated eagerly so a bad value fails at construction, not at render time.
    """

    workspace_id: str
    resource_id: str
    display_name: str
    kind: str

    def __post_init__(self) -> None:
        if self.kind not in _VALID_KINDS:
            raise ValueError(
                f"AvdBookmark.kind must be one of {_VALID_KINDS!r}, got {self.kind!r}"
            )


def _bookmark_from_raw(raw: Any) -> AvdBookmark:
    """Convert one raw stored entry to an :class:`AvdBookmark`.

    Raises ``ValueError`` or ``TypeError`` for anything malformed; callers (namely
    :meth:`BookmarkStore.load`) are expected to catch those and skip the entry.
    """
    if not isinstance(raw, dict):
        raise TypeError(f"bookmark entry must be an object, got {type(raw).__name__}")

    missing = [field for field in _REQUIRED_FIELDS if field not in raw]
    if missing:
        raise ValueError(f"bookmark entry missing required field(s): {', '.join(missing)}")

    return AvdBookmark(
        workspace_id=raw["workspaceId"],
        resource_id=raw["resourceId"],
        display_name=raw["displayName"],
        kind=raw["kind"],
    )


def _bookmark_to_raw(bookmark: AvdBookmark) -> dict:
    return {
        "workspaceId": bookmark.workspace_id,
        "resourceId": bookmark.resource_id,
        "displayName": bookmark.display_name,
        "kind": bookmark.kind,
    }


class BookmarkStore:
    """Persists the Phase 0 AVD bookmark list via :class:`winonlinux.state_store.StateStore`.

    Parameters
    ----------
    state_home:
        Override for ``$XDG_STATE_HOME`` (primarily for tests), forwarded to ``StateStore``.
    """

    def __init__(self, *, state_home: Any = None) -> None:
        self._store = StateStore(
            _STORE_NAME,
            schema_version=_SCHEMA_VERSION,
            defaults={"bookmarks": []},
            state_home=state_home,
        )

    def load(self) -> list[AvdBookmark]:
        """Load the bookmark list.

        Returns an empty list (never raises) when the store does not exist yet. Any individual
        entry that fails validation is skipped and logged at WARNING -- one admin typo must not
        take down the whole list.
        """
        data = self._store.load()
        raw_entries = data.get("bookmarks", [])
        if not isinstance(raw_entries, list):
            logger.warning(
                "avd_bookmarks store has a non-list 'bookmarks' field; treating as empty"
            )
            return []

        bookmarks: list[AvdBookmark] = []
        for index, raw in enumerate(raw_entries):
            try:
                bookmarks.append(_bookmark_from_raw(raw))
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "skipping malformed AVD bookmark entry at index %d: %s", index, exc
                )
        return bookmarks

    def save(self, bookmarks: list[AvdBookmark]) -> None:
        """Persist ``bookmarks``, replacing whatever was stored before."""
        self._store.save({"bookmarks": [_bookmark_to_raw(b) for b in bookmarks]})


def group_by_workspace(bookmarks: list[AvdBookmark]) -> dict[str, list[AvdBookmark]]:
    """Group ``bookmarks`` by ``workspace_id``, preserving input order within each group.

    The returned dict's key order follows first-occurrence order of each ``workspace_id`` in
    ``bookmarks`` (guaranteed by Python's dict insertion-order semantics).
    """
    grouped: dict[str, list[AvdBookmark]] = {}
    for bookmark in bookmarks:
        grouped.setdefault(bookmark.workspace_id, []).append(bookmark)
    return grouped
