# Design: fix-shutdown-loop-hygiene

## Context

The idle-pump bridge runs asyncio and GTK on one thread; there is no second loop in production, ever — which is
precisely why both defects stayed invisible until the audit ran the suite on Python 3.12 (where per-test fresh
loops contend the import-time lock) and reasoned through quit-while-busy (where the loop closes before cancelled
tasks get to run their `except`/`finally`). Both fixes make the code honest about loop lifetime rather than
changing the one-loop model.

## Goals / Non-Goals

**Goals:**

- Quit delivers cancellation: every pending task's cleanup runs before the loop closes, within a bounded deadline.
- No asyncio primitive in the codebase outlives or precedes the loop that uses it.
- Unhandled task exceptions land in redacted logs, not raw stderr at GC time.

**Non-Goals:**

- No change to the pump mechanism, heartbeat, or `_run_once` usage (F-25 is a V3 measurement note; the private-API
  risk is tracked as an assumed fact and re-verified by the CI matrix, not engineered around here).
- No graceful-drain of in-flight Graph requests beyond the deadline — quit means quit; the bound exists so a hung
  request cannot hold the process.

## Decisions

- **Shutdown sequence:** `destroy_group` for every account → pump `_run_once` until all tasks are done or a 2 s
  deadline elapses → log any still-pending task by name → `uninstall()`. Two seconds is generous for
  `CancelledError` handlers that only close sockets/files, and small enough that quit never feels hung. The
  executor is joined with the same deadline via a bounded equivalent of `shutdown_default_executor`; a worker
  still stuck (e.g. mid-`requests.get`) is abandoned as a daemon-style exit with a WARNING naming it — today's
  behavior, minus the up-to-30 s silent lag, plus the log line.
- **Serializer becomes per-manager state:** `CacheWriteSerializer` is constructed in `AuthManager.__init__` and
  passed where needed, dying with the manager and therefore with its loop. Rejected alternative — lazy per-loop
  lock creation inside the singleton (`WeakKeyDictionary[loop, Lock]`): preserves the import-time global at the
  cost of hidden state keyed on loops, and the "process-wide by design" property the singleton documented is
  really "per token cache", which is exactly one-per-manager. The docstring's cross-account rationale moves with
  it. The 3.12-failing test (`test_sign_out_concurrent_with_in_flight_acquire_does_not_raise_keyerror`) becomes
  the regression pin without modification.
- **Exception handler via `loop.set_exception_handler`** in `install()`, logging `context["message"]` and the
  exception through the `winonlinux` logger (redaction applies structurally). It logs and continues — a background
  task dying must not take the app down; the future UI can subscribe to error state through
  `add-state-change-listeners` surfaces, not through the loop handler.

## Risks

- Pumping during `do_shutdown` re-enters application callbacks (listeners, done-callbacks) after teardown began;
  those must tolerate late invocation. The task registry's discard path already does (audited); the shutdown test
  asserts listener exceptions during drain are swallowed-and-logged.
- `shutdown_default_executor` with a timeout is 3.12+; on 3.11 the equivalent is a manual `ThreadPoolExecutor`
  join — implementation must branch or use the manual join on both.
