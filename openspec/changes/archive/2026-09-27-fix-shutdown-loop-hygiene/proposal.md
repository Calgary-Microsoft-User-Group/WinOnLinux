# Proposal: fix-shutdown-loop-hygiene

## Why

The 2026-09-22 audit (`codeaudit/2026-09-22.md`) found the D-18 event-loop integration correct in steady state but
wrong at the edges of the loop's lifetime. On quit, `do_shutdown` cancels pending tasks and immediately closes the
loop: `CancelledError` is never delivered, so no coroutine's `finally` runs, and the default executor is never
drained — an in-flight `run_blocking(requests.get, …)` thread can hold the process open for up to the 30 s request
timeout after the user quits (F-06, MEDIUM; this bites harder once launcher/session tasks with real cleanup
exist). Separately, the audit's test run proved a latent loop-affinity defect: `cache_write_serializer` is a
module-level singleton whose `asyncio.Lock` binds to the first event loop that contends it — the suite fails on
Python 3.12 (`171 passed, 1 failed`, reproducible) and production is safe only by the unpinned assumption of one
loop per process (F-09, MEDIUM). Finally, no loop exception handler is installed, so a task exception escaping the
app-level catch blocks surfaces only at GC time via asyncio's default handler, outside §10.7 redaction discipline
(F-24).

## What Changes

- **Orderly shutdown** (F-06): after cancelling task groups, `do_shutdown` pumps the loop (bounded by a stated
  deadline) until cancelled tasks have settled, then uninstalls the bridge; the default executor is joined with a
  bound so a stuck worker delays exit by at most the deadline, not the full request timeout.
- **Loop-affine serializer** (F-09): `cache_write_serializer` stops being an import-time singleton with a baked-in
  `asyncio.Lock`; the lock is created per `AuthManager` lifetime (or lazily per running loop), removing the
  cross-loop hazard the 3.12 suite run exposed. CI gains the 3.12 matrix entry in `add-audit-test-coverage`.
- **Loop exception handler** (F-24): the bridge installs a handler that routes unhandled task exceptions through
  the redacted logging setup, so nothing escapes to stderr unredacted at GC time.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `app-foundation`: shutdown ordering, executor drain bound, loop exception handler.
- `auth-accounts`: cache-write serializer loop affinity.

## Impact

- Code: `src/winonlinux/app.py`, `asyncio_bridge.py`, `auth_cache.py`, `auth_manager.py`; tests for shutdown
  `finally` delivery and serializer behavior across loops (the currently failing 3.12 test becomes the pin).
- Closes audit findings F-06, F-09 (code half; the CI-matrix half lives in `add-audit-test-coverage`), F-24.
  F-25 (idle-pump cost) is noted for V3 and needs no change.
- D-18 unchanged; this is its lifecycle made honest. File and link a `WinOnLinuxCode` Linear issue.
