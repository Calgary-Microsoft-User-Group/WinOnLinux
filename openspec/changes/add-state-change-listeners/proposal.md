# Proposal: add-state-change-listeners

## Why

The 2026-09-22 audit's Part C verdict (`codeaudit/2026-09-22.md`) is that the backend cannot drive spec §4.4's UI
states: background poll outcomes are unobservable — every `Failed` from a poll tick is logged and dropped, so the
Offline banner and status-chip updates have no signal (F-07) — and auth state transitions notify no one, so the
per-account ReauthRequired banner has nothing to subscribe to (F-08). The same theme extends to messaging: no error
type carries a user-presentable message, and `InteractiveSignInFailedError.raw_error_description` is raw AADSTS
text one attribute away from a dialog, inverting §9's raw-codes-to-logs rule (F-20). Finally, `StateStore` and
`BookmarkStore` are synchronous disk I/O with no stated calling convention, which the UI shell will otherwise call
from GTK handlers and block the main loop (F-21, D-18). These are the foundation gaps that are cheap now and
expensive to retrofit once `add-ui-shell` builds polling timers and ad-hoc string mapping around their absence —
the audit rated the listener pair MEDIUM for exactly that reason.

## What Changes

- **Enumeration results become observable** (F-07): `CloudPcProvider.add_result_listener(cb)` fires on every
  refresh outcome — foreground or poll tick, success or `Failed` — carrying `(home_account_id, result)`. The
  existing `Failed`-never-overwrites-`last_result` semantics are unchanged; the listener carries the failure.
- **Auth state transitions become observable** (F-08): `AuthManager.add_auth_state_listener(cb)` fires
  `(home_account_id, new_state)` on every transition (`apply_error`, `mark_active`, `mark_offline`, `sign_out`,
  interactive begin/finish), following the pattern `add_active_account_listener` already established.
- **Error types carry user-presentable text** (F-20): the `AuthError` family and `EnumerationResult` failures gain
  a `user_message` field populated from a single mapping table; raw MSAL/Graph detail is renamed to make its
  logs-only intent unmistakable (§9 messaging rules).
- **Store I/O calling convention** (F-21): `StateStore` and `BookmarkStore` document (and tests pin) that loop-
  thread callers go through `run_blocking`; docstrings state the contract now so the UI change inherits it.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `auth-accounts`: auth-state listener; `user_message` on the error taxonomy.
- `cloudpc-enumeration`: result listener on the provider.
- `app-foundation`: store I/O calling convention.

## Impact

- Code: `cloudpc_provider.py`, `auth_manager.py`, `auth_state.py`, `auth_errors.py`, `state_store.py`,
  `avd_bookmarks.py`; tests for listener firing, ordering, and message mapping.
- `add-ui-shell` should list this change as a dependency: §4.4's banners, chips, and toasts consume these signals
  instead of polling backend attributes on a second timer.
- Closes audit findings F-07, F-08, F-20, F-21. No §14 decision touched; D-18's single-loop model is what makes
  plain-callable listeners (no thread marshalling) sufficient.
- File and link a `WinOnLinuxCode` Linear issue; add a blocking relation from the `add-ui-shell` issue to it.
