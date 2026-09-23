# Tasks: fix-shutdown-loop-hygiene

## 1. Orderly shutdown (F-06)

- [ ] 1.1 In `do_shutdown`: after `destroy_group` calls, pump the loop until all tasks settle or 2 s elapses; log
      still-pending tasks by name; then `asyncio_bridge.uninstall()` — `app.py:348-355`, helper in
      `asyncio_bridge.py`
- [ ] 1.2 Bounded executor join within the same deadline (manual join compatible with 3.11; note
      `shutdown_default_executor(timeout=)` is 3.12+); WARNING naming an abandoned worker —
      `asyncio_bridge.py:141-154`
- [ ] 1.3 Tests: cancelled task's `finally` flag set before loop close; a `run_blocking` sleeper does not delay
      the simulated shutdown past the deadline and is logged

## 2. Serializer loop affinity (F-09)

- [ ] 2.1 Construct `CacheWriteSerializer` in `AuthManager.__init__`; thread it to the three mutation sites;
      remove the module-level singleton (move its cross-account docstring rationale onto the attribute) —
      `auth_cache.py:228-257`, `auth_manager.py:410,517,691`
- [ ] 2.2 Confirm `test_sign_out_concurrent_with_in_flight_acquire_does_not_raise_keyerror` passes in a full-suite
      run on Python 3.12 (the audit's reproducer becomes the regression pin; CI matrix entry lands in
      `add-audit-test-coverage`)

## 3. Loop exception handler (F-24)

- [ ] 3.1 `install()` sets a loop exception handler logging through the `winonlinux` logger (redaction applies
      structurally); log-and-continue semantics — `asyncio_bridge.py`
- [ ] 3.2 Test: a task raising with no awaiter produces a redacted log record and the loop keeps pumping

## 4. Sync

- [ ] 4.1 Comment on the `WinOnLinuxCode` Linear issue with the findings closed (F-06, F-09 code half, F-24)
