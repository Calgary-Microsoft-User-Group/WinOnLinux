# Tasks: fix-account-lifecycle

## 1. Sign-out teardown (F-05)

- [x] 1.1 `AuthManager.sign_out` destroys the account's task group after cache/state removal; app-layer sign-out
      entry point schedules it from outside the group — `auth_manager.py:405-433`, `app.py`
- [x] 1.2 `acquire_token_silently` (and every `self.accounts[...]` access in public methods) raises
      `AccountUnknownError(AuthError)` for a missing account — `auth_manager.py:496`
- [x] 1.3 Poll loop treats `AccountUnknownError` as terminal: stop polling, do not log-and-repeat —
      `cloudpc_provider.py:386-396`
- [x] 1.4 Tests: start polling → sign out → poll task cancelled, no further token calls; acquisition for removed
      account → typed error; sign-out invoked from within the account's own group does not deadlock

## 2. Per-account enumeration state (F-04)

- [x] 2.1 `last_result` becomes `dict[str, EnumerationResult]` keyed by `home_account_id`, preserving the
      not-overwritten-by-`Failed` semantics per key; adjust readers — `cloudpc_provider.py:208,269`
- [x] 2.2 Tests: A enumerates → B's first refresh fails → `Failed.previous_entries == []`; switch back to A → A's
      list intact

## 3. Unstarted-manager refusal (F-13)

- [x] 3.1 Add `AuthManagerNotStarted(AuthError)`; guard every public method before side effects (socket open at
      `auth_manager.py:641-643` moves behind the guard) — `auth_manager.py`, `auth_errors.py`
- [x] 3.2 Tests: unstarted manager → `add_account` raises typed error and no `LoopbackListener.start` occurs;
      `start()` raising `KeyringUnavailable` leaves later calls refusing typed

## 4. State-machine contract (F-14, F-19)

- [x] 4.1 DEVICE_CA_BLOCKED no-op-with-log guards in `mark_active`, `mark_offline`, `begin_silent_refresh` —
      `auth_state.py:171-189,208-212`
- [x] 4.2 `_acquire_token_silently_uncached` calls `begin_silent_refresh()` (legal from ACTIVE and OFFLINE);
      `apply_error` docstring names OFFLINE as a legal source state — `auth_manager.py`, `auth_state.py:185-196`
- [x] 4.3 Tests: each unguarded mutator on a blocked account leaves state unchanged; silent acquisition passes
      through SILENT_REFRESH and returns to ACTIVE; OFFLINE → SILENT_REFRESH → ACTIVE recovery path

## 5. Assumption register and spec sync (F-18)

- [x] 5.1 Add the MSAL cached-AT semantics ("serves an unexpired AT with zero network; applies its own expiry
      buffer") to spec.md §12's verification list alongside V1 — it is currently a code comment only
      (`auth_manager.py:528-535`)
- [x] 5.2 Comment on the `WinOnLinuxCode` Linear issue for this change with the findings closed (F-04, F-05,
      F-13, F-14, F-18, F-19)
