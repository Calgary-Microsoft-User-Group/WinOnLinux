"""Consistency tests for the packaging data (add-flatpak-packaging, tasks 1.1/1.2/2.2).

D-21 makes the application ID a single constant owned by app-foundation; every packaging file
must consume it verbatim, never redefine it. These tests pin that — plus the §5.8
portals-over-holes invariant — so drift between the code and the packaging shows up in CI,
not at first install.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import install_gi_stub

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
MANIFEST = REPO / "packaging" / "flatpak"


def _app_id() -> str:
    install_gi_stub()
    from winonlinux.app import APPLICATION_ID

    return APPLICATION_ID


def test_every_packaging_file_carries_the_d21_application_id():
    app_id = _app_id()

    desktop = (DATA / f"{app_id}.desktop").read_text(encoding="utf-8")
    assert f"Icon={app_id}" in desktop
    assert "Exec=winonlinux" in desktop  # matches pyproject's [project.scripts] entry

    metainfo = (DATA / f"{app_id}.metainfo.xml").read_text(encoding="utf-8")
    assert f"<id>{app_id}</id>" in metainfo
    assert f'"desktop-id">{app_id}.desktop' in metainfo

    assert (DATA / "icons" / "hicolor" / "scalable" / "apps" / f"{app_id}.svg").exists()

    manifest = (MANIFEST / f"{app_id}.yml").read_text(encoding="utf-8")
    assert f"app-id: {app_id}" in manifest
    assert "command: winonlinux" in manifest


def test_pyproject_declares_the_winonlinux_entry_point():
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'winonlinux = "winonlinux.app:main"' in pyproject


def test_manifest_declares_no_blanket_filesystem_access():
    """§5.8 portals-over-holes (design decision 3): per-folder portal grants only -- a
    --filesystem= permission appearing in the manifest is a security-posture regression."""
    manifest = (MANIFEST / f"{_app_id()}.yml").read_text(encoding="utf-8")
    for line in manifest.splitlines():
        stripped = line.strip()
        if stripped.startswith("- --"):
            assert not stripped.startswith("- --filesystem"), (
                "the manifest must not grant static filesystem access (spec.md section 5.8)"
            )
    # And the permissions it DOES need are present.
    for expected in (
        "--socket=wayland",
        "--socket=fallback-x11",
        "--share=network",
        "--talk-name=org.freedesktop.secrets",
        "--socket=pulseaudio",
    ):
        assert expected in manifest


def test_freerdp_module_pins_tag_and_commit_together():
    """The Phase 1 module is pinned by tag AND commit (verified 2026-09-29); a tag without a
    commit is re-resolvable upstream and therefore not a pin."""
    module = (MANIFEST / "freerdp-module.yml").read_text(encoding="utf-8")
    assert "tag: '3.30.0'" in module
    assert "commit: 6b107f0aadbabc47941c5a5b893b88c01792af6d" in module
    assert "getCloudPcLaunchInfo" not in module  # unrelated, but cheap to keep honest
