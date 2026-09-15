# Design: add-flatpak-packaging

## Context

The packaging decision is made: Flatpak primary (D-3, §5.8), driven by the FreeRDP ≥ 3.30.0 floor (§2.2) that
distributions do not meet, with `.deb`/`.rpm` secondary and AppImage not planned. The client binary to bundle is
decided: `xfreerdp` (D-20). What is *not* yet verified is the sandbox's real behavior — Gate STACK verification V2
(portal availability, proxy exposure §5.9, X11-socket grantability) runs against the first real build this change
produces, and a bad result fires D-3's revisit trigger (§14.4). This change therefore has two jobs: produce the
artifact, and produce the evidence.

Fact status per the repo's convention: Flatpak-as-primary is **decided**; every §5.8 portal cost is **stated but
unverified** until V2; FreeRDP 3.30.0 as a buildable module is **assumed** (post-cutoff fact, §12 risk 14).

## Goals / Non-Goals

**Goals:**

- One installable Flatpak artifact covering the NFR-7 baseline (current+previous Ubuntu LTS, current+previous
  Fedora, current Debian stable; glibc ≥ 2.35; GNOME and KDE).
- Manifest permissions exactly matching §5.8 — no broader.
- Bundled `xfreerdp` ≥ 3.30.0 from Phase 1; Phase 0 builds may omit it since the native path is disabled anyway.
- deb/rpm packaging with a hard FreeRDP ≥ 3.30.0 dependency, shipped only where satisfiable.
- CI job building the Flatpak per-commit (§13.4 — build always runs; live-tenant tests never do).
- Executed V2 verification with recorded results.

**Non-Goals:**

- Granting USB redirection — not grantable under Flatpak; the recorded reason USB is out of scope (§5.5).
- Relying on distro-packaged FreeRDP — NFR-8's runtime check exists precisely because that is not relied upon.
- Choosing the FreeRDP backend — decided, `xfreerdp` (D-20).
- Store/distribution marketing assets beyond required AppStream metadata.

## Decisions

1. **GNOME runtime as the Flatpak base.** D-16 fixes GTK4/libadwaita + PyGObject, which the GNOME runtime ships;
   the alternative (freedesktop runtime + hand-built GTK stack) adds maintenance for nothing. FreeRDP and its X11
   client are built as manifest modules pinned to a tag ≥ 3.30.0 with checksummed sources.
2. **Sockets: `fallback-x11` + `wayland` + explicit `x11` evaluation at V2.** The shell can be Wayland-native, but
   the `xfreerdp` child needs X11. Whether `fallback-x11` suffices for a child process on a Wayland session — or a
   hard `--socket=x11` is required — is exactly the §5.8 verification item; the manifest carries the conservative
   choice V2 proves out. Without a grantable X11 path the Flatpak can sign in but not open native sessions, which
   is a D-3 revisit fact, not something to paper over.
3. **Portals over holes.** Secret Service, OpenURI, and filesystem access go through portals; no `--filesystem=home`
   escape hatch, because §5.5/§5.8 deliberately offer per-folder grants only. If the Secret Service portal is
   absent the app refuses sign-in per D-2 — packaging must not add a fallback the security design rejected.
4. **Application ID fixed here and never changed.** GTK application uniqueness supplies FR-4-AC-7 (D-18) and is
   keyed to the ID; the ID also names the keyring collection and XDG paths (D-13). Chosen once, recorded in the
   manifest, desktop file, and AppStream data.
5. **deb/rpm are thin.** Native packages declare `freerdp >= 3.30` (exact package names per distro) and install the
   same application tree; where the dependency cannot be satisfied the package is simply not shipped for that
   release, per §5.8. The runtime check (NFR-8) still guards the installed result.
6. **CI builds the Flatpak, does not test sessions.** Per §13.4 the CI boundary is unit/fixture; the packaging job
   proves the artifact assembles and installs. Session behavior on the E-8 matrix stays manual (§13.2).

## Risks / Trade-offs

- [V2 finds a portal cost worse than §5.8 states] → The affected §5.5 capability is removed knowingly; if the
  X11 socket or Secret Service portal fails outright, that fires D-3's revisit trigger — surfaced, not worked
  around.
- [Proxy environment not visible inside the sandbox (§5.9)] → V2 explicitly tests `HTTP_PROXY`/desktop proxy
  exposure under Flatpak; a negative result becomes a documented limitation and a Gate STACK input.
- [Bundled FreeRDP drifts behind upstream fixes] → Manifest pins a tag; a release checklist item re-pins on each
  app release. NFR-8 keeps under-floor installs from silently degrading.
- [Flatpak keyring interaction differs across GNOME/KDE (D-2, V1 adjacency)] → V2 runs on both desktops in the E-8
  matrix; failures route to the §6.3 report-and-refuse path, never a weaker cache.
- [deb/rpm matrix creep] → Bounded by §5.8: shipped only where the FreeRDP dependency is satisfiable, and the
  Flatpak remains the supported answer everywhere else.

## Open Questions

- Distribution channel: Flathub vs repo-hosted remote (affects publisher metadata and release cadence) — decided
  during this change, recorded per the §14 convention if it constrains anything downstream.
- Exact distro package names/versions satisfying FreeRDP ≥ 3.30.0 for the secondary formats — enumerated when the
  first deb/rpm ships.
