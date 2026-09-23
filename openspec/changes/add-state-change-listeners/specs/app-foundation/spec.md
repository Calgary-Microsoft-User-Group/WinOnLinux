# app-foundation — store I/O calling convention

Records the calling convention for synchronous persistence APIs before the first GTK-thread caller exists (audit
2026-09-22 finding F-21; D-18, spec.md §4.4 responsiveness).

## ADDED Requirements

### Requirement: Persistence I/O does not block the shared loop thread

`StateStore.load()`/`save()` and `BookmarkStore.load()` are synchronous disk I/O (including fsync). Their
docstrings SHALL state that loop-thread callers dispatch through `run_blocking`, and all in-repo loop-thread call
sites SHALL comply. The rule exists because a fsync on slow or network-backed `$HOME` stalls both asyncio and GTK
under D-18's shared loop.

#### Scenario: Loop-thread save

- **WHEN** code running on the event loop persists state
- **THEN** the write is dispatched through `run_blocking`, and the loop (and therefore GTK) is not blocked on the
  fsync
