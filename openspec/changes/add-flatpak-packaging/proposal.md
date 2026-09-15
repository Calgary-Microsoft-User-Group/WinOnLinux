# Proposal: add-flatpak-packaging

## Why

§2.2 requires FreeRDP ≥ 3.30.0 while distributions ship older, so the application must bundle FreeRDP — and Flatpak
is the decided way to make that bundling routine across the NFR-7 baseline (D-3, §5.8). Packaging also fixes the
sandbox surface the rest of the product depends on: keyring, browser handoff, folder grants, audio, and the X11
socket that D-20's `xfreerdp` requires. The skeleton lands early so Phase 0 can ship web-only from the same
artifact the native path later fills in.

## What Changes

- New **Flatpak manifest** as the primary distribution format (D-3): bundles the application, and from Phase 1,
  **`xfreerdp` ≥ 3.30.0** (D-20) with its X11 dependencies — one artifact across the NFR-7 platform baseline.
- **Sandbox permissions**, each an accepted cost recorded in §5.8:
  - Secret Service portal — token cache in the OS keyring (D-2; unavailable keyring → §6.3's report-and-refuse
    path, load-bearing not theoretical);
  - OpenURI portal — web launch (browser choice is the portal's, §10.5);
  - Filesystem portal — per-folder grants only for drive redirection; whole-home redirection is not offered (§5.5);
  - Audio socket — audio out / microphone;
  - **X11 socket** (`--socket=x11`, or `fallback-x11` plus `wayland`) — required by D-20's X11 client; on Wayland
    hosts the session runs through XWayland (§5.6).
- **USB redirection is not grantable under Flatpak** — recorded as the reason USB is out of scope (§5.5, §5.8).
- **Secondary `.deb`/`.rpm`** with a hard FreeRDP ≥ 3.30.0 dependency, shipped only where that is satisfiable;
  AppImage not planned; distro-packaged FreeRDP never relied on (NFR-8's runtime check backstops it).
- **Desktop integration**: desktop file, icon, AppStream metadata; application ID fixed early since GTK application
  uniqueness (FR-4-AC-7, D-18) hangs off it.
- **CI Flatpak build** so the artifact exists per-commit even though live-tenant tests do not run in CI (§13.4).
- **Gate STACK verification hooks** (V2): a real Flatpak build against which portal availability, proxy exposure
  (§5.9), and X11-socket grantability are confirmed — results feed §14.4 and can fire D-3's revisit trigger.

## Capabilities

### New Capabilities

- `packaging`: building and distributing the application — Flatpak primary with bundled FreeRDP and the §5.8
  permission set, deb/rpm secondary, platform baseline conformance, and desktop integration.

### Modified Capabilities

<!-- none — this is a new capability; no existing spec's requirements change -->

## Impact

- **Depends on**: `add-app-foundation` (application ID, runtime layout, XDG state paths per D-13). Full value —
  bundled FreeRDP actually used — arrives with `add-native-launcher`; until then the Flatpak ships the web-only
  Phase 0 client.
- **Bounds other work**: the portal costs bound §5.5's channel set; anything that proves harder than §5.8 states
  removes a capability from §5.5 knowingly rather than late. V2 findings can fire D-3's revisit trigger.
- **Unverified facts relied on** (per §12 risk 14 and §14.4): Flatpak portal/proxy/X11-socket exposure is
  **unverified until V2 runs against a real build**; FreeRDP 3.30.0 availability as a buildable Flatpak module is
  assumed and confirmed during manifest work.
- **Affected systems**: CI pipeline (new Flatpak build job), release process (Flathub or repo-hosted remote —
  distribution channel decided during this change), user documentation (§5.6 Wayland limitations, §5.9 proxy
  posture, keyring requirement).
