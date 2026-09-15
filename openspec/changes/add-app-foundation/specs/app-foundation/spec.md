# app-foundation Specification

## ADDED Requirements

### Requirement: Single application instance

The application SHALL enforce single-instance execution: a second launch MUST activate the existing instance's
main window and exit, so that two processes never share the token cache (FR-4-AC-7, §6.3).

#### Scenario: Second launch activates the running instance

- **WHEN** the application is already running and the user launches it again
- **THEN** the running instance's main window is presented and the second process exits without initializing a
  token cache or state store

### Requirement: Single asyncio event loop with per-account task groups

The application SHALL run one asyncio event loop integrated with the GTK main loop (D-18). All asynchronous
work belonging to an account SHALL run inside a task group keyed by that account's home account ID, and
cancelling a group SHALL cancel that account's in-flight work without affecting other accounts (supports
FR-3-AC-2). Blocking keyring and subprocess calls SHALL run in a thread executor, never on the loop thread.

#### Scenario: Account task group cancellation is isolated

- **WHEN** the task group for account A is cancelled while account B has in-flight tasks
- **THEN** every pending task owned by account A is cancelled and every task owned by account B runs to
  completion unaffected

#### Scenario: Blocking call does not stall the loop

- **WHEN** a keyring operation that blocks for 500 ms is invoked
- **THEN** the GTK main loop continues to process events during the operation because it executes in the
  thread executor

### Requirement: Persisted state store with versioned forward-only migrations

Non-secret application state SHALL persist as JSON under `XDG_STATE_HOME` with a `schemaVersion` field per
store and forward-only migrations (D-13). Writes SHALL be atomic. Tokens and any secret material MUST NOT be
written to the state store (§6.3, §10.2).

#### Scenario: Older store version is migrated forward

- **WHEN** the application loads a store whose `schemaVersion` is lower than the current version
- **THEN** each migration step is applied in order and the store is rewritten at the current version with no
  data loss for fields the migrations carry forward

#### Scenario: Newer store version fails safe

- **WHEN** the application loads a store whose `schemaVersion` is higher than the current version
- **THEN** the store is not modified and the application continues with defaults rather than crashing or
  truncating the file

#### Scenario: Secrets are rejected by the state layer

- **WHEN** a caller attempts to persist a value identified as secret material through the state store API
- **THEN** the write is refused and the incident is logged with the value redacted

### Requirement: Log redaction

The logging subsystem SHALL redact tokens, `Authorization` headers, and full `.rdpw` contents at all log
levels, and SHALL redact UPNs at the default level (§10.7). Redaction SHALL be enforced centrally by the
logging layer, not by caller discipline.

#### Scenario: Token never reaches a log sink

- **WHEN** any component logs a message containing an access token, refresh token, or `Authorization` header
  at any level
- **THEN** the emitted record contains a redaction marker in place of the secret value

#### Scenario: UPN redacted at default level

- **WHEN** a message containing a UPN is logged at the default level
- **THEN** the emitted record replaces the UPN with a redaction marker

### Requirement: FreeRDP runtime version probe

The application SHALL detect at runtime whether `xfreerdp` is present and whether its version meets the
3.30.0 floor, exposing the result (present, version, meets-floor) to consumers, so the native method can be
disabled with the specific reason rather than attempted (NFR-8, §9). The probe SHALL run off the loop thread
and cache its result for the app session.

#### Scenario: FreeRDP below the floor is reported, not attempted

- **WHEN** the installed `xfreerdp` reports a version below 3.30.0
- **THEN** the probe result states the detected version and that the floor is not met, and no native launch
  path treats FreeRDP as available

#### Scenario: FreeRDP absent

- **WHEN** no `xfreerdp` binary is found on the configured path
- **THEN** the probe result reports absence and the application continues running with the native method
  unavailable

### Requirement: Startup and footprint instrumentation

The application SHALL record cold-start-to-interactive-window time and SHALL provide a repeatable procedure
for measuring idle RSS, so NFR-1 (≤ 2 s p95) and NFR-4 (≤ 250 MB RSS) are measurable against PyGObject —
verification V3, which can fire D-16's revisit trigger.

#### Scenario: Cold-start timing is captured

- **WHEN** the application starts from a cold launch to an interactive main window
- **THEN** the elapsed time is recorded in the log (subject to redaction rules) in a form usable for the V3
  measurement

### Requirement: CI test boundary

The repository's CI SHALL run unit and fixture tests only; tests requiring a live tenant, a real session, or a
display server SHALL be excluded from per-commit CI and runnable on demand (§13.4).

#### Scenario: CI passes with no network or display

- **WHEN** the CI pipeline runs on a clean runner with no network access to Microsoft services and no display
  server
- **THEN** the full CI test suite executes and its outcome does not depend on those absent resources
