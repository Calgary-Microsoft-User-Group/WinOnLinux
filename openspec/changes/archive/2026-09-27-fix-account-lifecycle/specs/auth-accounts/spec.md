# auth-accounts — account lifecycle hardening

Closes the lifecycle edges found by the 2026-09-22 audit (F-05, F-13, F-14, F-19): sign-out teardown, typed
refusal from an unstarted manager, complete DEVICE_CA_BLOCKED terminality, and the SILENT_REFRESH pass-through
(spec.md §6.4, §6.5, FR-3-AC-3, D-2, D-18).

## ADDED Requirements

### Requirement: Sign-out tears down all of the account's background work

`sign_out(home_account_id)` SHALL cancel and destroy the account's task group (D-18) in addition to removing its
tokens and state, so no task belonging to a signed-out account continues to run or to hold its data (FR-3-AC-3).
Token acquisition for an unknown or removed account SHALL raise a typed `AuthError`, never an untyped exception.

#### Scenario: Sign-out while polling

- **WHEN** enumeration polling is running for the active account and that account is signed out
- **THEN** the poll task is cancelled as part of sign-out and no further Graph or token calls occur for the account

#### Scenario: Acquisition for a removed account

- **WHEN** `acquire_token_silently` is called with a `home_account_id` no longer in the account set
- **THEN** a typed `AuthError` subclass is raised, not `KeyError`

### Requirement: An unstarted manager refuses operations with a typed error and no side effects

If `start()` has not completed (including a `KeyringUnavailable` failure, D-2), every public `AuthManager`
operation SHALL raise a typed `AuthError` (`AuthManagerNotStarted`) before producing any side effect — in
particular, before any loopback listener socket is opened.

#### Scenario: Sign-in attempted after keyring refusal

- **WHEN** `start()` raised `KeyringUnavailable` and a caller invokes `add_account()`
- **THEN** `AuthManagerNotStarted` is raised and no socket is bound

### Requirement: DEVICE_CA_BLOCKED is terminal against every mutator

All state mutators — `mark_active`, `mark_offline`, `begin_silent_refresh`, `apply_error`, `begin_interactive` —
SHALL leave a DEVICE_CA_BLOCKED account in DEVICE_CA_BLOCKED; only `sign_out()` moves away from it (§6.5, §9).
Guards on the non-interactive mutators SHALL no-op with a log line rather than raise.

#### Scenario: Late network error on a blocked account

- **WHEN** any code path calls `mark_offline()` or `mark_active()` on a DEVICE_CA_BLOCKED account
- **THEN** the state remains DEVICE_CA_BLOCKED and the attempt is logged

### Requirement: Silent acquisition passes through SILENT_REFRESH

Silent token acquisition SHALL move the account through the SILENT_REFRESH state (entered from ACTIVE or OFFLINE)
so §6.4's refresh activity is observable, and `apply_error`'s documented legal source states SHALL match its real
callers (including OFFLINE).

#### Scenario: Observable refresh

- **WHEN** a silent acquisition runs for an ACTIVE account
- **THEN** the account's state passes through SILENT_REFRESH and returns to ACTIVE on success

## MODIFIED Requirements

_The `add-auth-account-manager` sign-out requirement is extended by the task-group teardown above; its
remove-tokens-and-state semantics are unchanged._
