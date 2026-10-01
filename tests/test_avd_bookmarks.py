"""Unit tests for winonlinux.avd_bookmarks (spec.md section 11: Phase 0 AVD bookmarks)."""

from __future__ import annotations

import logging

import pytest

from winonlinux.avd_bookmarks import (
    AvdBookmark,
    BookmarkStore,
    group_by_workspace,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolated_xdg_state_home(tmp_path, monkeypatch):
    """Never touch the real filesystem outside tmp_path (mirrors test_state_store.py)."""
    state_home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    return state_home


# -- AvdBookmark validation ----------------------------------------------------


def test_avd_bookmark_accepts_desktop_kind():
    bookmark = AvdBookmark(
        workspace_id="ws-1", resource_id="res-1", display_name="Dev Desktop", kind="desktop"
    )
    assert bookmark.kind == "desktop"


def test_avd_bookmark_accepts_remoteapp_kind():
    bookmark = AvdBookmark(
        workspace_id="ws-1", resource_id="res-2", display_name="Notepad", kind="remoteapp"
    )
    assert bookmark.kind == "remoteapp"


def test_avd_bookmark_rejects_invalid_kind():
    with pytest.raises(ValueError):
        AvdBookmark(
            workspace_id="ws-1",
            resource_id="res-1",
            display_name="Bad Entry",
            kind="desktopp",  # typo
        )


# -- BookmarkStore round-trip ---------------------------------------------------


def test_load_on_empty_store_returns_empty_list(tmp_path):
    store = BookmarkStore(state_home=tmp_path / "explicit-state-home")
    assert store.load() == []


def test_save_then_load_round_trips_bookmarks(tmp_path):
    state_home = tmp_path / "explicit-state-home"
    bookmarks = [
        AvdBookmark(
            workspace_id="ws-1", resource_id="res-1", display_name="Dev Desktop", kind="desktop"
        ),
        AvdBookmark(
            workspace_id="ws-1", resource_id="res-2", display_name="Notepad", kind="remoteapp"
        ),
        AvdBookmark(
            workspace_id="ws-2", resource_id="res-3", display_name="QA Desktop", kind="desktop"
        ),
    ]

    write_store = BookmarkStore(state_home=state_home)
    write_store.save(bookmarks)

    # A *fresh* instance pointed at the same state_home must see the same data.
    read_store = BookmarkStore(state_home=state_home)
    loaded = read_store.load()

    assert loaded == bookmarks


def test_save_then_load_round_trips_guid_shaped_ids(tmp_path):
    # Regression test: real Azure workspaceId/resourceId values are routinely bare GUIDs (e.g.
    # "9c495a02-4d4c-4f2a-8c8e-2b2c9e2f2222"), which otherwise match StateStore's opaque-token
    # secret-shape heuristic (36 chars, all in its charset) and would be spuriously refused by
    # StateStore.save() -- see state_store.py's _UUID_PATTERN carve-out. This must round-trip like
    # any other bookmark, not raise SecretValueRejected.
    state_home = tmp_path / "explicit-state-home"
    bookmark = AvdBookmark(
        workspace_id="9c495a02-4d4c-4f2a-8c8e-2b2c9e2f2222",
        resource_id="1a2b3c4d-5e6f-4a1b-9c8d-0e1f2a3b4c5d",
        display_name="Prod Desktop",
        kind="desktop",
    )

    write_store = BookmarkStore(state_home=state_home)
    write_store.save([bookmark])  # must not raise

    read_store = BookmarkStore(state_home=state_home)
    assert read_store.load() == [bookmark]


def test_load_skips_malformed_entry_but_keeps_valid_ones(tmp_path, caplog):
    state_home = tmp_path / "explicit-state-home"
    good_bookmark = AvdBookmark(
        workspace_id="ws-1", resource_id="res-1", display_name="Dev Desktop", kind="desktop"
    )

    # Save one valid bookmark first, then hand-corrupt the underlying store file to add a
    # malformed entry alongside it (missing required field + an invalid kind), simulating an
    # admin typo without going through BookmarkStore.save (which would itself validate).
    write_store = BookmarkStore(state_home=state_home)
    write_store.save([good_bookmark])

    raw_data = write_store._store.load()
    raw_data["bookmarks"].append({"workspaceId": "ws-2", "resourceId": "res-9"})  # missing fields
    raw_data["bookmarks"].append(
        {
            "workspaceId": "ws-3",
            "resourceId": "res-10",
            "displayName": "Weird",
            "kind": "not-a-real-kind",
        }
    )
    write_store._store.save(raw_data)

    read_store = BookmarkStore(state_home=state_home)
    with caplog.at_level(logging.WARNING, logger="winonlinux.avd_bookmarks"):
        loaded = read_store.load()

    assert loaded == [good_bookmark]

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 2
    joined = "\n".join(r.getMessage() for r in warning_records)
    assert "malformed" in joined.lower()


# -- group_by_workspace ----------------------------------------------------------


def test_group_by_workspace_groups_and_preserves_order():
    a = AvdBookmark(workspace_id="ws-1", resource_id="res-1", display_name="A", kind="desktop")
    b = AvdBookmark(workspace_id="ws-2", resource_id="res-2", display_name="B", kind="desktop")
    c = AvdBookmark(workspace_id="ws-1", resource_id="res-3", display_name="C", kind="remoteapp")
    d = AvdBookmark(workspace_id="ws-1", resource_id="res-4", display_name="D", kind="remoteapp")

    grouped = group_by_workspace([a, b, c, d])

    assert list(grouped.keys()) == ["ws-1", "ws-2"]
    assert grouped["ws-1"] == [a, c, d]  # input order preserved within the group
    assert grouped["ws-2"] == [b]


def test_group_by_workspace_empty_input_returns_empty_dict():
    assert group_by_workspace([]) == {}
