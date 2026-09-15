# Tasks: add-flatpak-packaging

## 1. Application identity and skeleton

- [ ] 1.1 Fix the application ID (reverse-DNS) and record it — it also keys GTK uniqueness (FR-4-AC-7, D-18),
      keyring naming (D-2), and XDG state paths (D-13); coordinate with add-app-foundation
- [ ] 1.2 Write the desktop file, icon, and AppStream metadata under that ID

## 2. Flatpak manifest

- [ ] 2.1 Author the manifest on the GNOME runtime matching D-16's GTK4/libadwaita + PyGObject stack
- [ ] 2.2 Declare the §5.8 permission set exactly: Secret Service portal, OpenURI portal, filesystem portal
      (per-folder only), audio socket, and the X11 display path (`fallback-x11` + `wayland`, hard `x11` if V2
      requires it); no blanket filesystem access
- [ ] 2.3 Add the FreeRDP module pinned to a tag ≥ 3.30.0 with checksummed sources, building `xfreerdp` and its
      X11 dependencies (Phase 1 builds; Phase 0 builds may omit the module)
- [ ] 2.4 Confirm FreeRDP 3.30.0 actually exists as pinned (post-cutoff fact, §12 risk 14) before relying on the
      tag

## 3. Gate STACK verification V2 (evidence, not assumption)

- [ ] 3.1 On a real build, verify Secret Service portal availability on GNOME and KDE; confirm the D-2
      report-and-refuse path when absent
- [ ] 3.2 Verify OpenURI and filesystem portal behavior against the §5.8 cost table
- [ ] 3.3 Verify the X11 socket is grantable and a bundled `xfreerdp` child opens a window on X11 and on Wayland
      (XWayland); record a negative result against D-3's revisit trigger
- [ ] 3.4 Verify proxy exposure inside the sandbox (`HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`, desktop settings)
      per §5.9 and record what the app can observe
- [ ] 3.5 Write the V2 findings up for Gate STACK and update §14.4; remove any §5.5 capability whose portal cost
      proved worse than stated — knowingly, in the spec

## 4. Secondary formats

- [ ] 4.1 Enumerate which NFR-7 distributions can satisfy FreeRDP ≥ 3.30.0 natively; ship deb/rpm only there
- [ ] 4.2 Build deb/rpm with the hard versioned dependency and the same application tree; no AppImage

## 5. CI and release

- [ ] 5.1 Add a per-commit CI job building (and install-smoke-testing) the Flatpak — §13.4 boundary: no
      live-tenant or session tests in CI
- [ ] 5.2 Decide and record the distribution channel (Flathub vs repo-hosted remote) with its publisher metadata
- [ ] 5.3 Add a release-checklist item to re-pin the FreeRDP module each app release
- [ ] 5.4 Document in user-facing docs: keyring requirement (D-2), §5.6 Wayland/XWayland limitations, §5.9 proxy
      posture and the PAC/authenticated-proxy deferral
