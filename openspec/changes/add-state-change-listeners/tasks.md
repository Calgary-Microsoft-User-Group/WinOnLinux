# Tasks: add-state-change-listeners

## 1. Provider result listener (F-07)

- [ ] 1.1 `CloudPcProvider.add_result_listener`/`remove_result_listener`; notify on every outcome from
      `refresh_now` (and thus every poll tick and `refresh_after_action`), catching and logging listener
      exceptions — `cloudpc_provider.py`
- [ ] 1.2 Tests: poll tick raising `GraphError` notifies with `Failed` carrying previous entries while
      `last_result` stays untouched; success notifies `Enumerated`; a raising listener does not prevent other
      listeners or corrupt the result

## 2. Auth state listener (F-08)

- [ ] 2.1 Inject a transition callback into `AccountAuthState` (fired from the single point `_log_transition`
      already passes through); expose `AuthManager.add_auth_state_listener`/`remove_auth_state_listener` —
      `auth_state.py`, `auth_manager.py`
- [ ] 2.2 Tests: INVALID_GRANT classification fires `(id, REAUTH_REQUIRED)`; DEVICE_CA_BLOCKED fires exactly once;
      sign_out fires; listener exceptions logged, transition unaffected

## 3. User-presentable messages (F-20)

- [ ] 3.1 Add `user_message` to the `AuthError` family and failure-shaped `EnumerationResult` types, populated
      from one mapping table (§9 wording: proxy named for `GraphProxyError`, no-licence text, keyring refusal) —
      `auth_errors.py`, `auth_manager.py`, `cloudpc_provider.py`
- [ ] 3.2 Rename `raw_error_description` → `raw_error_description_for_logs` — `auth_manager.py:201-204`
- [ ] 3.3 Test: no `user_message` in the table contains `AADSTS` or a raw HTTP status; every public error type has
      a non-empty `user_message`

## 4. Store I/O convention (F-21)

- [ ] 4.1 Docstrings on `StateStore.load`/`save` and `BookmarkStore.load`: "loop-thread callers dispatch through
      `run_blocking`"; audit existing call sites in `app.py` for compliance — `state_store.py`, `avd_bookmarks.py`
- [ ] 4.2 Test: representative loop-thread persist path dispatches through the executor (mirrors the existing
      `run_blocking` offload test)

## 5. Sync

- [ ] 5.1 Add a dependency note to `add-ui-shell`'s proposal (consumes these listeners instead of polling) and a
      Linear blocking relation from the `add-ui-shell` issue to this change's issue
- [ ] 5.2 Comment on this change's `WinOnLinuxCode` Linear issue with the findings closed (F-07, F-08, F-20, F-21)
