# Proposal: add-native-launcher

## Why

Native sessions are the product's core differentiator over "just use the browser": FR-2 requires a per-resource
native connect method, and FR-2-AC-4 defines Phase 1 done as a visible RDP session from a cold start with no manual
file handling. Everything below the connection configuration is already implemented by FreeRDP (§2.2) — what the
application owns is the launcher layer: invoking `xfreerdp` correctly, safely, and observably. This change builds
that layer on top of the Connection-Config Provider (`add-connection-config-provider`).

## What Changes

- New **Native launcher** component (§5.1): given a validated `.rdpw` from the Connection-Config Provider, launch
  **`xfreerdp`** (D-20) as a **subprocess** (D-1, §5.1.1) with the upstream AVD invocation
  `<rdpw> /gateway:type:arm /sec:aad`; never link `libfreerdp`.
- **Runtime FreeRDP version check** (NFR-8): FreeRDP ≥ 3.30.0 enforced before any launch; below the floor or absent,
  the native method is disabled with the detected version and required floor stated — never attempted.
- **Config handoff hygiene** (§10.1, §5.1.1): the `.rdpw` is written to an app-generated path with `0600`
  permissions in a user-private directory and deleted once the subprocess has started.
- **Argument-handling rules** (§10.4): argument vector only, never a shell; fixed option set the application
  controls; feed-sourced values appear only inside the config file, never on the command line.
- **Session lifecycle**: child-process tracking, exit-status → §9 error-taxonomy mapping (coarse by design — the
  full exit-code taxonomy is backlog item G-44 and granularity insufficiency is D-1's revisit trigger),
  stopped/deallocated Cloud PC handled as a **starting state** with the §7.4 timeout, session-drop reconnect with
  config **re-acquisition and re-validation** (§9), and native-failure → web fallback in one action (FR-2-AC-5).
- **Proxy propagation** (§5.9): the launcher explicitly passes proxy configuration to the FreeRDP subprocess — it
  does not inherit the client's HTTP proxy settings — preventing the auth-succeeds-connect-fails failure mode.
- **Redirection defaults** (§5.5): clipboard text and audio out on; drive redirection off with an explicit
  per-resource folder picker; microphone off; printers/smartcard/camera deferred; USB out of scope. Flag names are
  verified against `xfreerdp --help` at 3.30 before being relied on, not assumed.
- **Display scope** (§5.6): single monitor, windowed and fullscreen, `/dynamic-resolution` where supported, integer
  scaling only. `xfreerdp` is an X11 client: on Wayland the session always runs through XWayland, and the §5.6
  limitations are permanent for Phase 1.
- **Concurrency** (NFR-5): at least 2 concurrent native sessions; soft warning above 4.

## Capabilities

### New Capabilities

- `native-launch`: launching, supervising, and terminating FreeRDP subprocess sessions for the native connection
  method — version floor enforcement, secure config handoff, argument handling, exit mapping, proxy propagation,
  redirection and display flags.

### Modified Capabilities

<!-- none — this is a new capability; no existing spec's requirements change -->

## Impact

- **Depends on**: `add-app-foundation` (process model, state store, logging/redaction §10.7) and
  `add-connection-config-provider` (the `.rdpw` source; `Unavailable(reason)` drives the disabled state).
- **Gated**: implementable only after Stage 1 (feed schema) and Stage 2 (Connection-Config Provider) complete —
  Phase 1, Stage 3 of the roadmap (§11.1). Exit criterion is FR-2-AC-4 plus a recorded NFR-6 result and the §5.6
  display-backend confirmation.
- **Packaging**: requires bundled `xfreerdp` ≥ 3.30.0 at runtime (`add-flatpak-packaging`, D-3) and the X11 socket
  under Flatpak (§5.8).
- **Security surface**: feed-derived data crossing a process boundary (§10.4) and the short-lived config file on
  disk (§10.1) are the two controls this change owns; both carry unit/fixture coverage (§13.2, §13.3).
- **Unverified facts relied on**: FreeRDP 3.30.0 contents/release and §5.5 flag names are post-cutoff claims
  (§12 risk 14) — re-verified at Stage 0/Stage 3 before this change's tasks begin.
