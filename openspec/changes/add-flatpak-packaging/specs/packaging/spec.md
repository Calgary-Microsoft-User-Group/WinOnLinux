# packaging

## ADDED Requirements

### Requirement: Flatpak is the primary distribution format

The project SHALL ship a Flatpak as its primary artifact (D-3, §5.8), and from Phase 1 the Flatpak SHALL bundle
`xfreerdp` ≥ 3.30.0 (D-20) with its X11 dependencies so that one artifact serves the entire NFR-7 baseline without
relying on distro-packaged FreeRDP.

#### Scenario: Install on a distro shipping old FreeRDP

- **WHEN** the Flatpak is installed on an NFR-7 baseline distribution whose repositories carry FreeRDP older than
  3.30.0
- **THEN** the bundled `xfreerdp` satisfies the NFR-8 version check and the native method is not disabled for
  version reasons

#### Scenario: Phase 0 web-only build

- **WHEN** a Phase 0 Flatpak build is produced before the native path exists
- **THEN** the artifact installs and runs the web-only client, with the native method shown disabled with reason
  (FR-2-AC-1)

### Requirement: Sandbox permissions match the accepted cost table exactly

The Flatpak manifest SHALL request only the §5.8 permission set: Secret Service portal (token cache), OpenURI
portal (web launch), filesystem portal for per-folder grants, audio socket, and an X11 display path (`--socket=x11`
or `fallback-x11` plus `wayland`). It SHALL NOT request blanket filesystem access, and USB redirection SHALL remain
ungranted — the recorded reason USB is out of scope (§5.5).

#### Scenario: Drive redirection grant

- **WHEN** the user picks a folder for drive redirection on a resource
- **THEN** access is obtained through the filesystem portal for that folder only, and no whole-home access exists
  in the manifest

#### Scenario: Keyring unavailable inside the sandbox

- **WHEN** no Secret Service provider is reachable through the portal
- **THEN** the application reports it plainly and does not sign in (D-2, §6.3) — packaging supplies no weaker
  fallback store

#### Scenario: Web launch through the portal

- **WHEN** the user invokes Connect (Web)
- **THEN** the URL opens via the OpenURI portal and the browser choice is the portal's (§10.5)

### Requirement: The X11 display path for native sessions is verified, not assumed

The packaging work SHALL verify on a real Flatpak build that the X11 socket is grantable and that a bundled
`xfreerdp` child can open a session window on both X11 and Wayland (XWayland) hosts — Gate STACK verification V2.
A negative result SHALL be recorded against D-3's revisit trigger rather than worked around (§5.8, §14.4).

#### Scenario: Native session from inside the sandbox on Wayland

- **WHEN** a native session is launched from the Flatpak on a Wayland desktop
- **THEN** the `xfreerdp` window opens through XWayland using the granted X11 path

#### Scenario: X11 path not grantable

- **WHEN** V2 finds the X11 socket cannot be granted in a target environment
- **THEN** the finding is recorded as firing D-3's revisit trigger and surfaced at Gate STACK, and the build is not
  shipped as if native sessions worked

### Requirement: Proxy exposure inside the sandbox is verified at Gate STACK

The packaging work SHALL confirm what proxy configuration (`HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` and desktop proxy
settings) the Flatpak sandbox actually exposes to the application (§5.9) and SHALL record the result as a Gate
STACK input; until verified, sandbox proxy behavior is treated as unknown, not assumed working.

#### Scenario: Proxy visibility check

- **WHEN** the V2 verification runs on a host with a system proxy configured
- **THEN** the recorded result states which proxy sources the sandboxed application could observe, and any gap is
  documented as a limitation rather than discovered by users

### Requirement: Secondary native packages carry a hard FreeRDP floor

`.deb` and `.rpm` packages SHALL declare a hard dependency on FreeRDP ≥ 3.30.0 and SHALL be shipped only for
releases where that dependency is satisfiable from the target distribution (§5.8). AppImage SHALL NOT be produced.
The NFR-8 runtime check remains in force regardless of install channel.

#### Scenario: Target distro cannot satisfy the floor

- **WHEN** a target distribution has no FreeRDP ≥ 3.30.0 package available
- **THEN** no deb/rpm is published for it and the Flatpak is the supported install path

### Requirement: Desktop integration and a stable application ID

The package SHALL install a desktop file, icon, and AppStream metadata under a single application ID that is fixed
at introduction and never changed; the ID SHALL be the same one used for GTK application uniqueness (FR-4-AC-7,
D-18) and for keyring/XDG state naming (D-13).

#### Scenario: Second launch via the desktop file

- **WHEN** the application is launched from the desktop entry while an instance is already running
- **THEN** the running instance is activated and no second process shares the token cache (FR-4-AC-7)

### Requirement: CI builds the Flatpak

CI SHALL build the Flatpak artifact per-commit; the build proves assembly and installability only — live-tenant,
session, and display testing remain on-demand per the §13.4 CI boundary.

#### Scenario: Manifest regression

- **WHEN** a commit breaks the Flatpak manifest or module build
- **THEN** CI fails on the packaging job before release, with no live-tenant access required
