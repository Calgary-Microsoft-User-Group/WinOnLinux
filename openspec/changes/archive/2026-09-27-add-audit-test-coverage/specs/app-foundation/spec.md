# app-foundation — continuous verification across supported Pythons

Turns two audit-era assumptions into CI-verified facts (audit 2026-09-22 findings F-09 verification half, F-22;
spec.md §13.4).

## ADDED Requirements

### Requirement: CI exercises every supported Python minor

CI SHALL run the unit suite on every CPython minor the project declares support for (currently 3.11, 3.12, 3.13),
so `requires-python` is never broader than what is tested. The event-loop bridge's private-API dependence and all
asyncio-primitive loop affinity are thereby continuously verified across the declared range.

#### Scenario: New minor breaks a private API

- **WHEN** a supported CPython minor changes behavior the bridge or a lock depends on
- **THEN** CI fails on that matrix entry before any release, rather than the breakage surfacing in an audit or in
  the field

### Requirement: Second-process constructor purity is pinned headlessly

The application class's constructor SHALL construct no stateful object (token cache, state store, listener, task
registry); all stateful construction SHALL occur in first-activation. This half of the single-instance safety
contract (FR-4-AC-7) SHALL be pinned by a headless unit test; the D-Bus forwarding half remains covered by the
manual procedure (§13.4).

#### Scenario: Constructor purity

- **WHEN** the application object is instantiated without activation
- **THEN** no cache, store, socket, or task-registry state exists yet
