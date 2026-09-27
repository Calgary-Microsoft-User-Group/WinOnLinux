# auth-accounts — loopback robustness pinned

Gives the loopback listener's docstring-asserted limits their scenarios (audit 2026-09-22 finding F-10; spec.md
§10.3, D-6). The behavior is already implemented; these requirements make it testable contract.

## ADDED Requirements

### Requirement: Oversized and stalled connections cannot deny the real redirect

A request line exceeding the 16 KiB cap SHALL be rejected with the invalid-request response without consuming the
single-accept slot or shortening the overall deadline. A connection that sends no complete request line SHALL be
released at the per-connection read timeout (10 s), leaving the listener able to accept and complete the genuine
redirect within the deadline measured from `start()`.

#### Scenario: Oversized request line

- **WHEN** a client sends a request line longer than 16 KiB
- **THEN** it receives the invalid-request response, and a subsequent correct redirect on a new connection is
  accepted and completes the flow

#### Scenario: Slowloris alongside the real redirect

- **WHEN** one connection stalls without completing a request line while the browser delivers the genuine redirect
  on another
- **THEN** the genuine redirect succeeds within the deadline and the stalled connection is closed at the read
  timeout
