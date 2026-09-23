# Design: fix-account-lifecycle

## Context

The audit verified the auth state machine's transition table is correct (offline-vs-reauth, cancel semantics,
claims bounds, DEVICE_CA_BLOCKED against classified errors) — these findings are all at the *edges*: what happens
when an account is removed, switched, or never started. D-18's task groups exist precisely so per-account teardown
is one call; the defects are call sites that don't make it.

## Goals / Non-Goals

**Goals:**

- An account's background work dies with the account (`sign_out` → task-group teardown, FR-3-AC-3).
- No API on `AuthManager` or `CloudPcProvider` ever surfaces another account's data or an untyped exception for a
  lifecycle-shaped failure.
- The state machine's documented contract matches its real call graph, including the SILENT_REFRESH pass-through.

**Non-Goals:**

- No backoff-retry inside silent refresh (§6.4's "retry with backoff" arrow): the poll loop already provides the
  periodic retry, and the audit classified AT retention as delegated-to-MSAL. This change *records* that
  delegation as a tracked assumption rather than adding a second retry layer; revisit if V1's msal-extensions
  verification surfaces contrary MSAL behavior.
- No change to single-flight, claims handling, or error classification — verified correct.
- No UI notification of these transitions — that is `add-state-change-listeners`.

## Decisions

- **`sign_out` owns group teardown.** `AuthManager.sign_out` calls `task_registry.destroy_group(home_account_id)`
  after cache removal, mirroring how `switch_active_account` already cancels the outgoing account. Alternative —
  making the app layer stop polling — rejected: every future caller of `sign_out` would have to remember it, which
  is how F-05 happened.
- **`AuthManagerNotStarted(AuthError)`, checked first.** A dedicated subclass rather than re-raising the stashed
  `KeyringUnavailable`, because "not started" also covers pre-`start()` misuse, and the stashed original remains
  available on the app attribute for the UI's message. The guard runs before `LoopbackListener.start()` so no
  socket is opened for a sign-in that cannot proceed.
- **Unknown account → `AccountUnknownError(AuthError)`** from `acquire_token_silently` (and any method doing
  `self.accounts[...]`), converting the F-05 symptom's `KeyError` into something the poll loop's error handling
  can classify. The poll loop treats it as terminal for the task (stop, don't log-and-repeat).
- **`last_result` becomes `dict[str, EnumerationResult]`** with the existing not-overwritten-by-`Failed` semantics
  per key. Alternative — clearing on account change — rejected: it destroys the FR-1-AC-4 "previous list stays
  visible" behavior for the account you switch *back* to.
- **Terminal-state guards are no-op-with-log, not raise.** `mark_active`/`mark_offline`/`begin_silent_refresh` on
  a DEVICE_CA_BLOCKED account log and return; raising would turn a future caller's bug into a crash in an error
  path. `begin_interactive` keeps its existing raise (deliberate user action deserves a hard refusal).
- **`begin_silent_refresh()` called from `_acquire_token_silently_uncached`,** entering SILENT_REFRESH from
  ACTIVE/OFFLINE, restoring §6.4's observable state; `apply_error`'s docstring amended to name OFFLINE as a legal
  source state (it already behaves correctly from it).

## Risks

- Destroying the task group inside `sign_out` while the caller itself may be running inside that group: the
  app-layer sign-out entry point must schedule the call from outside the group (same pattern
  `switch_active_account` already handles). Pinned by a test.
