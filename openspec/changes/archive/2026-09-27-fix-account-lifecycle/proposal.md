# Proposal: fix-account-lifecycle

## Why

The 2026-09-22 code audit (`codeaudit/2026-09-22.md`) found a cluster of MEDIUM/LOW lifecycle defects where account
boundaries leak: `sign_out` removes the account but never cancels its task group, leaving an orphaned poll loop
failing with an untyped `KeyError` every 60 s forever (F-05, violating FR-3-AC-3 and the purpose D-18 gives task
groups); the provider's single account-agnostic `last_result` bleeds one account's Cloud PCs into another account's
`Failed.previous_entries` after a switch (F-04, violating FR-3 isolation); an `AuthManager` whose `start()` failed
with `KeyringUnavailable` fails closed but as raw `AttributeError` — after opening a loopback socket — instead of a
typed refusal (F-13); DEVICE_CA_BLOCKED terminality is guarded on only two of five state mutators (F-14); and the
`SILENT_REFRESH` state is unreachable while `apply_error` is invoked outside its documented contract (F-19). None
of these is visible in a single-account happy path, which is exactly why they need spec scenarios now, before the
UI shell builds on the current behavior.

## What Changes

- **Sign-out cancels the account's work** (F-05): `sign_out` destroys the account's task group (covering the poll
  loop), and `acquire_token_silently` for an unknown/removed account raises a typed `AuthError`, never `KeyError`.
- **Enumeration state becomes per-account** (F-04): `last_result` is keyed by `home_account_id`;
  `Failed.previous_entries` only ever carries the same account's prior entries.
- **Unstarted manager refuses with typed errors before side effects** (F-13): every public `AuthManager` method
  checks `start()` completed and raises `AuthManagerNotStarted` (an `AuthError`) before any socket is opened —
  making the D-2 refusal state enforceable at the API boundary rather than a convention.
- **DEVICE_CA_BLOCKED guarded on every mutator** (F-14): `mark_active`, `mark_offline`, and `begin_silent_refresh`
  gain the same terminal-state guard `apply_error` and `begin_interactive` already have (§6.5: nothing escapes but
  `sign_out()`).
- **Silent-refresh state contract reconciled** (F-19): silent acquisition passes through `SILENT_REFRESH` (making
  §6.4's state observable for the future UI) and `apply_error`'s documented preconditions match its real callers.
  The audit's associated ASSUMED fact — MSAL serves cached unexpired ATs with zero network (F-18) — is recorded in
  the spec.md §12 verification list rather than silently trusted.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `auth-accounts`: sign-out task-group teardown, typed unstarted-manager refusal, complete DEVICE_CA_BLOCKED
  guards, SILENT_REFRESH wiring.
- `cloudpc-enumeration`: per-account enumeration state.

## Impact

- Code: `src/winonlinux/auth_manager.py`, `auth_state.py`, `cloudpc_provider.py`, `app.py` (sign-out wiring);
  tests for every scenario.
- Spec: §12's verification list gains the MSAL cached-AT assumption (joins V1's msal-extensions scope). No §14
  decision is touched.
- Closes audit findings F-04, F-05, F-13, F-14, F-18 (register entry), F-19.
- Independent of unapplied changes; recommended before `add-ui-shell` (which will otherwise inherit F-04/F-05's
  visible symptoms). File and link a `WinOnLinuxCode` Linear issue per repo convention.
