# auth-accounts — observable auth state and user-presentable errors

Adds the notification surface spec §4.4's per-account UI states require (audit 2026-09-22 findings F-08, F-20;
spec.md §4.4, §9 messaging rules).

## ADDED Requirements

### Requirement: Auth state transitions are observable per account

The auth manager SHALL expose `add_auth_state_listener` / `remove_auth_state_listener`; registered callbacks SHALL
be invoked with `(home_account_id, new_state)` on every state transition, on the event-loop thread. A listener
exception SHALL be caught and logged and SHALL NOT affect the transition or other listeners.

#### Scenario: Background invalid_grant raises the banner signal

- **WHEN** a background silent acquisition classifies as INVALID_GRANT and the account moves to REAUTH_REQUIRED
- **THEN** every registered listener is invoked with that account id and REAUTH_REQUIRED — the §4.4 per-account
  banner needs no polling

#### Scenario: Terminal state fires once

- **WHEN** an account enters DEVICE_CA_BLOCKED
- **THEN** listeners fire exactly once for that state; subsequent classified errors on the blocked account produce
  no further transitions or notifications

### Requirement: Errors carry user-presentable text; raw codes stay in logs

Every `AuthError` surfaced to callers SHALL carry a `user_message` suitable for direct display, mapped from one
table; raw protocol detail (AADSTS codes, MSAL `error_description`) SHALL be exposed only under a name that marks
it logs-only (§9).

#### Scenario: No AADSTS in dialog text

- **WHEN** any auth error's `user_message` is rendered
- **THEN** it contains no AADSTS code or raw MSAL description; the raw detail is available separately for logs
