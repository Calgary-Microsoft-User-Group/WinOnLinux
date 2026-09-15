# Change: add-app-foundation

## Why

Every other change in this plan — auth, enumeration, launchers, actions, UI — needs a process to live in. The
stack is already decided (Python 3 + GTK4/libadwaita per D-16, asyncio per D-18), but no application skeleton
exists yet. This change builds the foundation those decisions describe, so subsequent capabilities land as
modules inside a running, testable application rather than each re-inventing process structure, state storage,
logging, and concurrency.

## What Changes

- New repository application layout: `pyproject.toml`, `src/` package, Apache-2.0 `LICENSE` (D-12), unit-test
  harness, and a CI pipeline that runs unit/fixture tests only (§13.4).
- GTK4/libadwaita application shell via PyGObject (D-16): `Adw.Application` with application uniqueness, so a
  second launch activates the running instance rather than starting a competing process (FR-4-AC-7).
- Single asyncio event loop integrated with the GTK main loop; per-account task groups keyed by home account
  ID; blocking keyring and subprocess calls confined to a thread executor (D-18).
- Persisted-state store: JSON files under `XDG_STATE_HOME` with a `schemaVersion` per store and forward-only
  migrations; tokens explicitly excluded — they belong to the keyring only (D-13, §6.3).
- Logging subsystem enforcing the §10.7 redaction rules: tokens, `Authorization` headers, and full `.rdpw`
  contents redacted at all levels; UPNs redacted at the default level.
- FreeRDP runtime version probe: detect the installed `xfreerdp` and parse its version against the 3.30.0
  floor, exposing the result for NFR-8 enforcement by the native launcher (which consumes, not implements,
  the probe).
- Startup instrumentation for NFR-1 (cold start ≤ 2 s p95) and a measurement hook for NFR-4 (idle RSS
  ≤ 250 MB), because both are D-16's revisit triggers and must be measurable from day one (verification V3).

## Capabilities

### New Capabilities

- `app-foundation`: application process lifecycle, single-instance behavior, asyncio concurrency model,
  persisted non-secret state, logging/redaction, and the FreeRDP version probe.

### Modified Capabilities

<!-- none — this is the first capability; no existing specs are modified -->

## Impact

- New code: application entry point, event-loop bridge, state store, logging setup, version probe module.
- New dependencies: PyGObject (GTK4/libadwaita), pytest for the unit harness. MSAL is **not** pulled in here —
  that belongs to `add-auth-account-manager`.
- Every later change depends on this one; its interfaces (task-group registry, state store, logger, probe
  result) are consumed by auth, providers, launchers, and the UI shell.
- Constraint carried forward: NFR-1/NFR-4 are decided-but-unverified for PyGObject; V3 measurement can fire
  D-16's revisit trigger (fallback: .NET + Avalonia + MSAL.NET). The skeleton must stay small enough that a
  stack reversal at Gate STACK is affordable.
