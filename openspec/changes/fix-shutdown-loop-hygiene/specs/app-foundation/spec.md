# app-foundation — loop lifecycle hygiene

Makes D-18's event-loop lifecycle honest at shutdown and for unhandled exceptions (audit 2026-09-22 findings F-06,
F-24).

## ADDED Requirements

### Requirement: Shutdown delivers cancellation before the loop closes

Application shutdown SHALL cancel all task groups, then run the loop until cancelled tasks have completed their
cleanup or a bounded deadline (2 s) elapses, then close the loop. Tasks still pending at the deadline SHALL be
logged by name. The default executor SHALL be joined within the same deadline; a worker still running afterwards
SHALL be abandoned with a WARNING rather than holding process exit.

#### Scenario: Cleanup runs on quit

- **WHEN** the user quits while a task with a `finally` block is pending
- **THEN** the `finally` executes before the loop closes, and no "Task was destroyed but it is pending" warning is
  emitted

#### Scenario: Stuck blocking call cannot hold exit

- **WHEN** the user quits while a `run_blocking` HTTP call is in flight
- **THEN** the process exits within the shutdown deadline, logging the abandoned worker, instead of waiting out
  the request timeout

### Requirement: Unhandled task exceptions are logged through redaction

The bridge SHALL install a loop exception handler routing unhandled task exceptions through the application's
redacted logging; they SHALL NOT surface via asyncio's default stderr handler at GC time, and the handler SHALL
log-and-continue, never terminate the application.

#### Scenario: Escaped task exception

- **WHEN** a task raises an exception no caller awaits
- **THEN** it appears in the redacted log at the next loop iteration, and the application keeps running

### Requirement: Event-loop primitives do not outlive their loop

No asyncio synchronization primitive SHALL be created at import time or shared across event-loop lifetimes;
primitives SHALL be owned by objects whose lifetime is within one loop's lifetime. (The concrete instance fixed
here is the token-cache write serializer — see the `auth-accounts` delta.)

#### Scenario: Fresh loop per test

- **WHEN** the unit suite runs each test in a fresh event loop on any supported Python minor (3.11–3.13)
- **THEN** no `RuntimeError: … is bound to a different event loop` occurs
